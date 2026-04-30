"""
capture both zivid and photoneo (small delay)
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
import msvcrt

import zivid
from harvesters.core import Harvester


OUTPUT_DIR = Path.home() / "Desktop" / "captures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ZIVID_SETTINGS_YAML: str | None = None 

PHOXI_CTI = (
    Path(os.environ.get("PHOXI_CONTROL_PATH", r"C:\Program Files\Photoneo\PhoXiControl"))
    / "API" / "bin" / "photoneo.cti"
)


def _save_zivid_ply(frame: zivid.Frame, ply_path: Path) -> None:
    """Save Zivid frame as PLY, trying multiple API spellings."""
    point_cloud = frame.point_cloud()
    for method_name in ("save", "save_ply"):
        if hasattr(point_cloud, method_name):
            getattr(point_cloud, method_name)(str(ply_path))
            return
    frame.save(str(ply_path))


class ZividCapture:
    def __init__(self, settings_yaml: str | None = None) -> None:
        print(f"[Zivid] zivid version: {zivid.__version__}")
        self.app = zivid.Application()

        cams = self.app.cameras()
        print(f"[Zivid] Found {len(cams)} camera(s)")
        if not cams:
            raise RuntimeError("No Zivid cameras detected.")

        self.camera = self.app.connect_camera()

        if settings_yaml and Path(settings_yaml).exists():
            self.settings = zivid.Settings.load(settings_yaml)
            print(f"[Zivid] Loaded settings from {settings_yaml}")
        else:
            self.settings = zivid.Settings(
                acquisitions=[zivid.Settings.Acquisition()],
                color=zivid.Settings2D(acquisitions=[zivid.Settings2D.Acquisition()]),
            )
            print("[Zivid] Using default settings")

        info = self.camera.info
        print(f"[Zivid] Connected: {info.model_name} (SN={info.serial_number})")

    def capture(self, ply_path: Path) -> bool:
        try:
            with self.camera.capture_2d_3d(self.settings) as frame:
                _save_zivid_ply(frame, ply_path)
            if ply_path.exists():
                print(f"[Zivid]    -> {ply_path.name} ({ply_path.stat().st_size:,} bytes)")
                return True
            print("[Zivid]    !! save returned but file does not exist")
            return False
        except Exception as e:
            print(f"[Zivid]    !! capture failed: {e}")
            traceback.print_exc()
            return False

    def close(self) -> None:
        try:
            self.camera.disconnect()
            print("[Zivid] Disconnected")
        except Exception:
            pass


class PhotoneoTrigger:
    def __init__(self, cti_path: Path) -> None:
        if not cti_path.exists():
            raise FileNotFoundError(f"photoneo.cti not found at: {cti_path}")
        self.h = Harvester()
        self.h.add_file(str(cti_path))
        self.h.update()
        if not self.h.device_info_list:
            raise RuntimeError("No Photoneo devices found.")
        for i, dev in enumerate(self.h.device_info_list):
            print(f"[Photoneo]   [{i}] {dev}")

        self.ia = self.h.create(0)
        nm = self.ia.remote_device.node_map
        try:
            nm.TriggerMode.value = "On"
            nm.TriggerSource.value = "Software"
        except Exception as e:
            print(f"[Photoneo] Trigger config note: {e}")
        self.ia.start()
        print("[Photoneo] Acquisition started")

    def trigger(self) -> bool:
        nm = self.ia.remote_device.node_map
        try:
            try:
                nm.TriggerSoftware.execute()
            except Exception:
                nm.TriggerFrame.execute()
            print("[Photoneo] -> trigger fired (PhoXi Control will save the frame)")
            return True
        except Exception as e:
            print(f"[Photoneo] !! trigger failed: {e}")
            return False

    def close(self) -> None:
        try:
            self.ia.stop()
            self.ia.destroy()
        except Exception:
            pass
        try:
            self.h.reset()
        except Exception:
            pass



def main() -> int:
    print(f"Output dir for Zivid: {OUTPUT_DIR}")
    print("Photoneo: see PhoXi Control's recording folder\n")

    zv: ZividCapture | None = None
    pn: PhotoneoTrigger | None = None

    try:
        zv = ZividCapture(ZIVID_SETTINGS_YAML)
    except Exception as e:
        print(f"[Zivid] FAILED to initialize: {e}")
        traceback.print_exc()

    print()

    try:
        pn = PhotoneoTrigger(PHOXI_CTI)
    except Exception as e:
        print(f"[Photoneo] FAILED to initialize: {e}")
        traceback.print_exc()

    if zv is None and pn is None:
        print("\nBoth cameras failed. Exiting.")
        return 1

    print()
    print("=" * 60)
    print("  [ENTER]      capture from both cameras")
    print("  [Q] + ENTER  quit cleanly")
    print("=" * 60)
    print()

    capture_count = 0

    def flush_stdin():
        """Discard any pending input that's buffered in stdin."""
        try:
            import msvcrt 
            while msvcrt.kbhit():
                msvcrt.getwch()
        except Exception:
            pass

    capture_count = 0
    print("[debug] entering main loop")


    while True:
        print(f"\nCapture #{capture_count + 1}? (Press SPACE/ENTER to capture, Q to quit) ", flush=True)

        ch = msvcrt.getwch()
        print(f"[debug] got key: {repr(ch)}")

        if ch.lower() == "q" or ch == "\x1b": 
            print("Quit requested.")
            break

        if ch not in (" ", "\r", "\n"):
            print(f"  (ignoring '{ch}', press SPACE/ENTER to capture or Q to quit)")
            continue

        capture_count += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"capture_{capture_count:04d}_{ts}"
        print(f"\n--- Capture #{capture_count} ({ts}) ---")
        t0 = time.monotonic()

        try:
            if zv is not None:
                zv.capture(OUTPUT_DIR / f"{stem}_zivid.ply")
            if pn is not None:
                pn.trigger()
        except Exception as e:
            print(f"[debug] exception during capture: {type(e).__name__}: {e}")
            traceback.print_exc()

        dt = time.monotonic() - t0
        print(f"--- done in {dt:.1f}s ---")

    print("[debug] exited main loop")
    print("\nShutting down...")
    if zv is not None:
        zv.close()
    if pn is not None:
        pn.close()

    print(f"\nTotal captures: {capture_count}")
    print(f"Zivid PLYs: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())