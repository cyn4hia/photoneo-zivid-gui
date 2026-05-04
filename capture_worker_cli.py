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
import traceback
from datetime import datetime
from pathlib import Path


def _phoxi_recording_dir(phoxi_dir: Path) -> Path:
    # EDIT this if PhoXi Control's recording folder is elsewhere.
    # Open PhoXi Control -> File -> Recording Options to confirm.
    return phoxi_dir / "Recordings"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def capture_zivid(out_dir: Path, settings_yaml: str | None) -> dict:
    try:
        import zivid
    except Exception as e:
        return {"status": "failed", "error": f"zivid import failed: {e}"}

    try:
        app = zivid.Application()
        cams = app.cameras()
        if not cams:
            return {"status": "failed", "error": "no Zivid cameras detected"}
        camera = app.connect_camera()

        if settings_yaml and Path(settings_yaml).exists():
            settings = zivid.Settings.load(settings_yaml)
        else:
            settings = zivid.Settings(
                acquisitions=[zivid.Settings.Acquisition()],
                color=zivid.Settings2D(
                    acquisitions=[zivid.Settings2D.Acquisition()],
                ),
            )
            try:
                settings.sampling.pixel = zivid.Settings.Sampling.Pixel.all
            except Exception:
                pass

        out_dir.mkdir(parents=True, exist_ok=True)
        ply_path = out_dir / "zivid.ply"
        zdf_path = out_dir / "zivid.zdf"
        with camera.capture_3d(settings) as frame:
            # Save the raw frame as ZDF first (preserves calibration + raw data)
            try:
                frame.save(str(zdf_path))
            except Exception as e:
                print(f"[zivid] zdf save failed: {e}", flush=True)

            # Then save a PLY for downstream tools that don't read ZDF
            point_cloud = frame.point_cloud()
            saved = False
            for method_name in ("save", "save_ply"):
                if hasattr(point_cloud, method_name):
                    getattr(point_cloud, method_name)(str(ply_path))
                    saved = True
                    break
            if not saved:
                frame.save(str(ply_path))

        ply_size = ply_path.stat().st_size if ply_path.exists() else 0
        zdf_size = zdf_path.stat().st_size if zdf_path.exists() else 0

        try:
            camera.disconnect()
        except Exception:
            pass

        return {
            "status": "complete",
            "file": str(ply_path),     # primary file (PLY)
            "zdf_file": str(zdf_path) if zdf_size > 0 else "",
            "size_bytes": ply_size,
            "zdf_size_bytes": zdf_size,
            "settings": {"yaml": settings_yaml or "default"},
        }

    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }


def capture_photoneo(phoxi_dir: Path) -> dict:
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

    new_file = None
    for _ in range(60):
        time.sleep(0.1)
        if rec_dir.exists():
            current = {p.name for p in rec_dir.glob("*.ply")}
            new_names = current - before_files
            if new_names:
                latest = max(new_names, key=lambda n: (rec_dir / n).stat().st_mtime)
                new_file = str(rec_dir / latest)
                break

    return {
        "status": "complete",
        "file": new_file or "",
        "phoxi_recording_dir": str(rec_dir),
        "note": ("file detected" if new_file else
                 "trigger fired but no new file; ensure PhoXi Control "
                 "Recording is ON with PLY format"),
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
        result = capture_photoneo(phoxi_dir)
        if result["status"] == "complete":
            if result.get("file"):
                files["photoneo"] = result["file"]
            settings["photoneo"] = {
                "phoxi_recording_dir": result.get("phoxi_recording_dir", ""),
                "note": result.get("note", ""),
            }
        else:
            errors.append(f"photoneo: {result.get('error', 'unknown')}")

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