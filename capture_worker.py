"""
one capture from both cameras
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import zivid
from harvesters.core import Harvester


OUTPUT_DIR = Path.home() / "Desktop" / "captures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ZIVID_SETTINGS_YAML: str | None = None

PHOXI_CTI = (
    Path(os.environ.get("PHOXI_CONTROL_PATH", r"C:\Program Files\Photoneo\PhoXiControl"))
    / "API" / "bin" / "photoneo.cti"
)


def _save_zivid_ply(frame, ply_path):
    point_cloud = frame.point_cloud()
    for method_name in ("save", "save_ply"):
        if hasattr(point_cloud, method_name):
            getattr(point_cloud, method_name)(str(ply_path))
            return
    frame.save(str(ply_path))


def capture_zivid(stem: str) -> None:
    print("[Zivid] starting...", flush=True)
    app = zivid.Application()
    cams = app.cameras()
    if not cams:
        print("[Zivid] !! no cameras found", flush=True)
        return
    camera = app.connect_camera()
    if ZIVID_SETTINGS_YAML and Path(ZIVID_SETTINGS_YAML).exists():
        settings = zivid.Settings.load(ZIVID_SETTINGS_YAML)
    else:
        settings = zivid.Settings(
            acquisitions=[zivid.Settings.Acquisition()],
        )

    settings.sampling.pixel = zivid.Settings.Sampling.Pixel.all
    settings.sampling.color = zivid.Settings.Sampling.Color.rgb

    ply_path = OUTPUT_DIR / f"{stem}_zivid.ply"

    with camera.capture_2d_3d(settings) as frame:
        _save_zivid_ply(frame, ply_path)
    if ply_path.exists():
        print(f"[Zivid] -> {ply_path.name} ({ply_path.stat().st_size:,} bytes)", flush=True)
    try:
        camera.disconnect()
    except Exception:
        pass


def trigger_photoneo() -> None:
    print("[Photoneo] starting...", flush=True)
    if not PHOXI_CTI.exists():
        print(f"[Photoneo] !! photoneo.cti not found at {PHOXI_CTI}", flush=True)
        return
    h = Harvester()
    h.add_file(str(PHOXI_CTI))
    h.update()
    if not h.device_info_list:
        print("[Photoneo] !! no devices found", flush=True)
        return
    ia = h.create(0)
    nm = ia.remote_device.node_map
    try:
        nm.TriggerMode.value = "On"
        nm.TriggerSource.value = "Software"
    except Exception:
        pass
    ia.start()
    try:
        try:
            nm.TriggerSoftware.execute()
        except Exception:
            nm.TriggerFrame.execute()
        print("[Photoneo] -> trigger fired (PhoXi Control will save)", flush=True)
    finally:
        try:
            ia.stop()
            ia.destroy()
        except Exception:
            pass
        try:
            h.reset()
        except Exception:
            pass


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    idx = sys.argv[1] if len(sys.argv) > 1 else "0001"
    stem = f"capture_{idx}_{ts}"
    print(f"=== Worker capture {stem} ===", flush=True)

    try:
        capture_zivid(stem)
    except Exception as e:
        print(f"[Zivid] !! {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    try:
        trigger_photoneo()
    except Exception as e:
        print(f"[Photoneo] !! {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    print(f"=== Worker done ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())