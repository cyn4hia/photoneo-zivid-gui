"""

Press SPACE  -> capture from both cameras, save PLYs
Press Q/ESC  -> quit cleanly

"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import zivid
from harvesters.core import Harvester

import keyboard  # global hotkey listener

# configs 
OUTPUT_DIR = Path("captures")
OUTPUT_DIR.mkdir(exist_ok=True)

ZIVID_SETTINGS: str | None = None

PHOXI_CTI = (
    Path(os.environ.get("PHOXI_CONTROL_PATH", r"C:\Program Files\Photoneo\PhoXiControl"))
    / "API" / "bin" / "photoneo.cti"
)

# hotkeys
CAPTURE_KEY = "space"
QUIT_KEYS = ("q", "esc")

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
            print("[Zivid] No YAML provided -- using Consumer Goods preset")
            self.settings = zivid.presets.read_camera_presets(
                self.camera, "Consumer"
            )[0].settings  # first preset for the category

        info = self.camera.info
        print(f"[Zivid] Connected: {info.model_name}  SN={info.serial_number}")

    def capture(self, ply_path: Path) -> None:
        with self.camera.capture_2d_3d(self.settings) as frame:
            frame.point_cloud().save(str(ply_path))
        print(f"[Zivid]   -> {ply_path}")

    def close(self) -> None:
        try:
            self.camera.disconnect()
        except Exception:
            pass

class PhotoneoCapture:
    def __init__(self, cti_path: Path) -> None:
        if not cti_path.exists():
            raise FileNotFoundError(
                f"Photoneo GenTL producer not found at: {cti_path}\n"
                "Check that PhoXi Control is installed and PHOXI_CONTROL_PATH is set."
            )

        print(f"[Photoneo] Loading GenTL producer: {cti_path}")
        self.h = Harvester()
        self.h.add_file(str(cti_path))
        self.h.update()

        if not self.h.device_info_list:
            raise RuntimeError(
                "[Photoneo] No devices found. Open PhoXi Control and make sure "
                "the scanner is connected (not just visible) before running this script."
            )


        for i, dev in enumerate(self.h.device_info_list):
            print(f"[Photoneo]   [{i}] {dev}")
        self.ia = self.h.create(0)

        nm = self.ia.remote_device.node_map
        try:
            nm.TriggerMode.value = "On"
            nm.TriggerSource.value = "Software"
        except Exception as exc:

            print(f"[Photoneo] Trigger config note: {exc}")

        try:
            nm.SendPointCloud.value = True
        except Exception:
            pass

        self.ia.start()
        print("[Photoneo] Acquisition started")

    def capture(self, ply_path: Path) -> None:

        nm = self.ia.remote_device.node_map
        try:
            nm.TriggerSoftware.execute()
        except Exception:

            nm.TriggerFrame.execute()

        with self.ia.fetch(timeout=30) as buf:
            xyz = self._extract_xyz(buf)

        self._write_ply(ply_path, xyz)
        print(f"[Photoneo] -> {ply_path}  ({xyz.shape[0]} points)")

    @staticmethod
    def _extract_xyz(buf) -> np.ndarray:
        """Pull XYZ float32 data out of the GenTL buffer payload."""
        comp = buf.payload.components[0]

        h, w = comp.height, comp.width
        arr = comp.data.reshape(h, w, 3).astype(np.float32, copy=True)

        mask = ~np.all(arr == 0, axis=2)
        return arr[mask]

    @staticmethod
    def _write_ply(path: Path, points: np.ndarray) -> None:
        n = points.shape[0]
        header = (
            "ply\n"
            "format binary_little_endian 1.0\n"
            f"element vertex {n}\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
        ).encode("ascii")
        with open(path, "wb") as f:
            f.write(header)
            f.write(points.astype("<f4").tobytes())

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
    zv: ZividCapture | None = None
    pn: PhotoneoCapture | None = None

    try:
        zv = ZividCapture(ZIVID_SETTINGS)
    except Exception as exc:
        print(f"[Zivid] FAILED to initialize: {exc}")

    try:
        pn = PhotoneoCapture(PHOXI_CTI)
    except Exception as exc:
        print(f"[Photoneo] FAILED to initialize: {exc}")

    if zv is None and pn is None:
        print("Both cameras failed to initialize. Exiting.")
        return 1

    print()
    print("=" * 60)
    print(f"  Press [{CAPTURE_KEY.upper()}] to capture from both cameras")
    print(f"  Press [Q] or [ESC] to quit")
    print("=" * 60)
    print()

    capture_count = 0
    last_trigger = 0.0
    DEBOUNCE_SEC = 0.5

    try:
        while True:
            if any(keyboard.is_pressed(k) for k in QUIT_KEYS):
                print("\nQuit requested.")
                break

            if keyboard.is_pressed(CAPTURE_KEY):
                now = time.monotonic()
                if now - last_trigger < DEBOUNCE_SEC:
                    time.sleep(0.05)
                    continue
                last_trigger = now

                capture_count += 1
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = f"capture_{capture_count:04d}_{ts}"
                print(f"\n--- Capture #{capture_count} ({ts}) ---")

                if zv is not None:
                    try:
                        zv.capture(OUTPUT_DIR / f"{stem}_zivid.ply")
                    except Exception as exc:
                        print(f"[Zivid] capture failed: {exc}")

                if pn is not None:
                    try:
                        pn.capture(OUTPUT_DIR / f"{stem}_photoneo.ply")
                    except Exception as exc:
                        print(f"[Photoneo] capture failed: {exc}")

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

    print(f"Done. {capture_count} captures saved to {OUTPUT_DIR.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())