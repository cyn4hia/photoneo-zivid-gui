"""
Headless capture worker for the GUI.

The GUI's services/capture_service.py spawns this script as a subprocess
for each capture, passing config as JSON in argv[1]. We do the camera work
and print one line back: CAPTURE_RESULT:{...json...}
"""

from __future__ import annotations

import json
import os
import sys
import time
import shutil
import traceback
from datetime import datetime
from pathlib import Path


def _phoxi_recording_dir(phoxi_dir: Path) -> Path:
    # EDIT this if PhoXi Control's recording folder is elsewhere.
    # Open PhoXi Control -> File -> Recording Options to confirm.
    return Path(r"D:\Images\data-collection\captures")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def capture_zivid(out_dir: Path, settings_yaml: str | None) -> dict:
    try:
        import zivid
    except Exception as e:
        return {"status": "failed", "error": f"zivid import failed: {e}"}

    try:
        from zivid_io import save_zivid_outputs_scaled
    except Exception as e:
        return {"status": "failed",
                "error": f"zivid_io import failed: {e}. "
                         "Make sure zivid_io.py is in the same folder."}

    try:
        app = zivid.Application()
        cams = app.cameras()
        if not cams:
            return {"status": "failed", "error": "no Zivid cameras detected"}
        camera = app.connect_camera()

        # Bare Settings() works for capture_2d_3d in this Zivid version.
        # Override with a YAML file if provided.
        if settings_yaml and Path(settings_yaml).exists():
            settings = zivid.Settings.load(settings_yaml)
        else:
            settings = zivid.Settings(
                acquisitions=[zivid.Settings.Acquisition()],
                color=zivid.Settings2D(
                    acquisitions=[zivid.Settings2D.Acquisition()],
                ),
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = out_dir / "zivid"

        frame = camera.capture_2d_3d(settings)

        written, depth_meta = save_zivid_outputs_scaled(
            frame,
            prefix=prefix,
            save_zdf=True,
            save_rgba_png=True,
            depth_mode="u16_scaled",   # change to "float_npy" if you want raw float
            save_snr_png=True,
            save_normal_png=True,
            save_ply=True,
        )

        try:
            camera.disconnect()
        except Exception:
            pass

        # Convert Path objects to str so we can JSON-serialize them
        files_dict = {k: str(v) for k, v in written.items()}

        # Sizes for the report
        sizes = {}
        for k, v in written.items():
            try:
                sizes[k] = Path(v).stat().st_size
            except Exception:
                sizes[k] = 0

        # Primary output for the GUI's main file slot is the PLY
        primary = files_dict.get("pc", "")

        return {
            "status": "complete",
            "file": primary,
            "outputs": files_dict,
            "sizes": sizes,
            "depth_meta": depth_meta,
            "settings": {"yaml": settings_yaml or "default"},
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }


def capture_photoneo(phoxi_dir: Path, out_dir: Path) -> dict:
    """Trigger Photoneo. PhoXi Control records the file; we identify it
    by snapshotting the recording folder before/after."""
    try:
        from harvesters.core import Harvester
    except Exception as e:
        return {"status": "failed", "error": f"harvesters import failed: {e}"}

    cti = phoxi_dir / "API" / "bin" / "photoneo.cti"
    if not cti.exists():
        return {
            "status": "failed",
            "error": f"photoneo.cti not found at {cti}",
        }

    rec_dir = _phoxi_recording_dir(phoxi_dir)
    before_files: set[str] = set()
    if rec_dir.exists():
        before_files = {p.name for p in rec_dir.glob("*.ply")}
        before_files |= {p.name for p in rec_dir.glob("*.praw")}

    h = None
    ia = None
    try:
        h = Harvester()
        h.add_file(str(cti))
        h.update()
        if not h.device_info_list:
            return {"status": "failed", "error": "no Photoneo devices found"}
        ia = h.create(0)
        nm = ia.remote_device.node_map
        try:
            nm.TriggerMode.value = "On"
            nm.TriggerSource.value = "Software"
        except Exception:
            pass
        ia.start()

        try:
            nm.TriggerSoftware.execute()
        except Exception:
            nm.TriggerFrame.execute()
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
    finally:
        if ia is not None:
            try:
                ia.stop()
                ia.destroy()
            except Exception:
                pass
        if h is not None:
            try:
                h.reset()
            except Exception:
                pass

    # Wait for PhoXi to write *something* new, with retries.
    new_paths: list[Path] = []
    deadline = time.time() + 12.0    # 12 second budget total
    while time.time() < deadline:
        time.sleep(0.3)
        if not rec_dir.exists():
            continue
        current_paths = []
        for ext in ("*.ply", "*.praw"):
            current_paths.extend(rec_dir.glob(ext))
        # New = filenames that weren't there before the trigger
        new_paths = [p for p in current_paths if p.name not in before_files]
        if new_paths:
            break

    moved_files: dict = {}
    move_errors: list = []

    if not new_paths:
        # Nothing new appeared; PhoXi didn't write
        return {
            "status": "complete",
            "file": "",
            "praw_file": "",
            "ply_file": "",
            "phoxi_recording_dir": str(rec_dir),
            "note": ("trigger fired but no new file appeared in "
                     f"{rec_dir}. Verify PhoXi Control Recording is ON "
                     "with PLY/PRAW format selected."),
        }

    # Move every new file into out_dir
    import shutil
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        move_errors.append(f"could not create out_dir {out_dir}: {e}")

    for src_path in new_paths:
        ext = src_path.suffix.lower().lstrip(".")
        dest = out_dir / f"photoneo.{ext}"

        # If a stale file exists at the destination, remove it first
        if dest.exists():
            try:
                dest.unlink()
            except Exception as e:
                move_errors.append(f"could not remove stale {dest}: {e}")

        # Wait briefly + retry: PhoXi may still be writing
        moved = False
        last_error: Exception | None = None
        for attempt in range(15):
            try:
                time.sleep(0.4)
                shutil.move(str(src_path), str(dest))
                moved_files[ext] = str(dest)
                moved = True
                break
            except Exception as e:
                last_error = e

        if not moved:
            move_errors.append(
                f"failed to move {src_path.name} -> {dest}: {last_error}"
            )
            # Last resort: fall back to original path so the file isn't lost
            moved_files[ext] = str(src_path)

    primary = moved_files.get("ply", "") or moved_files.get("praw", "")

    note_parts = [f"detected {len(new_paths)} new file(s) in {rec_dir}"]
    if move_errors:
        note_parts.append("ERRORS: " + "; ".join(move_errors))
    else:
        note_parts.append(f"moved into {out_dir}")

    return {
        "status": "complete",
        "file": primary,
        "praw_file": moved_files.get("praw", ""),
        "ply_file": moved_files.get("ply", ""),
        "phoxi_recording_dir": str(rec_dir),
        "out_dir": str(out_dir),
        "note": " · ".join(note_parts),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("CAPTURE_RESULT:" + json.dumps({
            "status": "failed", "error": "no payload"}))
        return 1

    try:
        payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print("CAPTURE_RESULT:" + json.dumps({
            "status": "failed", "error": f"bad json: {e}"}))
        return 1

    cameras: list[str] = [c.lower() for c in payload.get("cameras", [])]
    out_dir = Path(payload["out_dir"])
    phoxi_dir = Path(
        payload.get("phoxi_dir")
        or os.environ.get("PHOXI_CONTROL_PATH",
                          r"C:\Program Files\Photoneo\PhoXiControl")
    )
    zivid_settings = payload.get("zivid_settings")

    files: dict[str, str] = {}
    settings: dict[str, dict] = {}
    errors: list[str] = []

    if "zivid" in cameras:
        result = capture_zivid(out_dir, zivid_settings)
        if result["status"] == "complete":
            files["zivid"] = result["file"]
            settings["zivid"] = result.get("settings", {})
        else:
            errors.append(f"zivid: {result.get('error', 'unknown')}")

    if "photoneo" in cameras:
        result = capture_photoneo(phoxi_dir, out_dir)
        if result["status"] == "complete":
            if result.get("ply_file"):
                files["photoneo_ply"] = result["ply_file"]
            if result.get("praw_file"):
                files["photoneo_praw"] = result["praw_file"]

            if result.get("file"):
                files["photoneo"] = result["file"]
            settings["photoneo"] = {
                "phoxi_recording_dir": result.get("phoxi_recording_dir", ""),
                "note": result.get("note", ""),
            }
    if files and not errors:
        overall_status = "complete"
    elif files:
        overall_status = "partial"
    else:
        overall_status = "failed"

    out: dict = {
        "status": overall_status,
        "files": files,
        "camera_settings": settings,
        "timestamp": _now_iso(),
    }
    if errors:
        out["error"] = "; ".join(errors)

    print("CAPTURE_RESULT:" + json.dumps(out))
    return 0 if overall_status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())