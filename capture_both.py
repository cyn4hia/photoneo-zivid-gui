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

CAPTURE_KEY = "space"
QUIT_KEYS = ("q", "esc")
DEBOUNCE_SEC = 0.5



class ZividCapture:
    def __init__(self, settings_yaml: str | None = None) -> None:
        print("[Zivid] Starting application...")
        self.app = zivid.Application()
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
        print(f"[Zivid] Connected: {info.model_name}  SN={info.serial_number}")

    def capture(self, ply_path: Path) -> bool:
        try:
            with self.camera.capture_2d_3d(self.settings) as frame:
                frame.point_cloud().save(str(ply_path))
            size = ply_path.stat().st_size if ply_path.exists() else 0
            print(f"[Zivid]    -> {ply_path.name} ({size:,} bytes)")
            return True
        except Exception as e:
            print(f"[Zivid]    !! capture failed: {e}")
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
            raise FileNotFoundError(
                f"photoneo.cti not found at: {cti_path}\n"
                "Set PHOXI_CONTROL_PATH env var to your install dir."
            )

        print(f"[Photoneo] Loading GenTL producer: {cti_path}")
        self.h = Harvester()
        self.h.add_file(str(cti_path))
        self.h.update()

        if not self.h.device_info_list:
            raise RuntimeError(
                "No Photoneo devices found. Open PhoXi Control and connect "
                "the scanner first."
            )

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
        print("[Photoneo] Acquisition started (PhoXi Control will auto-save frames)")

    def trigger(self) -> bool:
        """Fire a software trigger. Don't fetch buffer - PhoXi Control saves the frame."""
        nm = self.ia.remote_device.node_map
        try:
            try:
                nm.TriggerSoftware.execute()
            except Exception:
                nm.TriggerFrame.execute()
            print("[Photoneo] -> trigger fired (check PhoXi Control's recording folder)")
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
            print("[Photoneo] Stopped")
        except Exception:
            pass



def main() -> int:
    print(f"Output dir for Zivid PLYs: {OUTPUT_DIR}")
    print()

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
    print(f"  [{CAPTURE_KEY.upper()}]  capture from both cameras")
    print(f"  [Q] / [ESC]  quit")
    print("=" * 60)
    print()

    capture_count = 0
    last_trigger = 0.0

    try:
        while True:
            if any(keyboard.is_pressed(k) for k in QUIT_KEYS):
                print("\nQuit requested.")
                break

            if keyboard.is_pressed(CAPTURE_KEY):
                if time.monotonic() - last_trigger < DEBOUNCE_SEC:
                    time.sleep(0.05)
                    continue
                last_trigger = time.monotonic()
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
                print(f"--- done in {dt:.1f}s ---")

                while keyboard.is_pressed(CAPTURE_KEY):
                    time.sleep(0.02)

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nInterrupted.")

    finally:
        print("\nShutting down cameras...")
        if zv is not None:
            zv.close()
        if pn is not None:
            pn.close()

    print(f"\nTotal captures: {capture_count}")
    print(f"Zivid PLYs:    {OUTPUT_DIR}")
    print(f"Photoneo PLYs: see PhoXi Control's recording folder")
    return 0


if __name__ == "__main__":
    sys.exit(main())