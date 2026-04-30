"""
capture both zivid and photoneo (small delay)
"""

from __future__ import annotations

import os
import sys
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import zivid
from harvesters.core import Harvester
import keyboard


OUTPUT_DIR = Path.home() / "Desktop" / "captures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ZIVID_SETTINGS_YAML: str | None = None  

PHOXI_CTI = (
    Path(os.environ.get("PHOXI_CONTROL_PATH", r"C:\Program Files\Photoneo\PhoXiControl"))
    / "API" / "bin" / "photoneo.cti"
)



def _save_zivid_ply(frame: zivid.Frame, ply_path: Path) -> None:
    """Save a Zivid frame as PLY, trying a few API spellings for compatibility."""
    point_cloud = frame.point_cloud()

    for method_name in ("save", "save_ply"):
        if hasattr(point_cloud, method_name):
            getattr(point_cloud, method_name)(str(ply_path))
            return

    frame.save(str(ply_path))


class ZividCapture:
    def __init__(self, settings_yaml: str | None = None) -> None:
        print(f"[Zivid] zivid module version: {zivid.__version__}")
        print("[Zivid] Starting application...")
        self.app = zivid.Application()

        cams = self.app.cameras()
        print(f"[Zivid] Found {len(cams)} camera(s):")
        for c in cams:
            print(f"  - {c.info.model_name} (SN={c.info.serial_number}, "
                  f"state.connected={c.state.connected})")

        if not cams:
            raise RuntimeError("No Zivid cameras detected.")

        print("[Zivid] Connecting to camera...")
        self.camera = self.app.connect_camera()

        if settings_yaml and Path(settings_yaml).exists():
            print(f"[Zivid] Loading settings from {settings_yaml}")
            self.settings = zivid.Settings.load(settings_yaml)
        else:
            print("[Zivid] Using built-in default settings")
            self.settings = zivid.Settings(
                acquisitions=[zivid.Settings.Acquisition()],
                color=zivid.Settings2D(acquisitions=[zivid.Settings2D.Acquisition()]),
            )

        info = self.camera.info
        print(f"[Zivid] Connected: {info.model_name} (SN={info.serial_number})")

    def capture(self, ply_path: Path) -> bool:
        try:
            print("[Zivid]    capturing...")
            with self.camera.capture_2d_3d(self.settings) as frame:
                print("[Zivid]    capture done, saving PLY...")
                _save_zivid_ply(frame, ply_path)
            if ply_path.exists():
                size = ply_path.stat().st_size
                print(f"[Zivid]    -> {ply_path.name} ({size:,} bytes)")
                return True
            else:
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

        print(f"[Photoneo] Loading GenTL producer: {cti_path}")
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


quit_event = threading.Event()
capture_event = threading.Event()
capture_lock = threading.Lock()


def on_space_press(_):
    capture_event.set()


def on_esc_press(_):
    print("\n[main] ESC pressed - quitting")
    quit_event.set()


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

    keyboard.on_press_key("space", on_space_press, suppress=False)
    keyboard.on_press_key("esc", on_esc_press, suppress=False)

    print()
    print("=" * 60)
    print("  [SPACE]  capture from both cameras")
    print("  [ESC]    quit cleanly")
    print("=" * 60)
    print()

    capture_count = 0

    try:
        while not quit_event.is_set():
            triggered = capture_event.wait(timeout=0.1)
            if not triggered:
                continue
            capture_event.clear()

            with capture_lock:
                if quit_event.is_set():
                    break
                capture_count += 1
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = f"capture_{capture_count:04d}_{ts}"
                print(f"\n--- Capture #{capture_count} ({ts}) ---")
                t0 = time.monotonic()

                if zv is not None:
                    zv.capture(OUTPUT_DIR / f"{stem}_zivid.ply")
                if pn is not None:
                    pn.trigger()

                dt = time.monotonic() - t0
                print(f"--- done in {dt:.1f}s, ready for next capture ---")

    except KeyboardInterrupt:
        print("\n[main] Interrupted (Ctrl+C)")

    finally:
        keyboard.unhook_all()
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