"""
Press SPACE to capture, Q/ESC to quit.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
from harvesters.core import Harvester
import keyboard


OUTPUT_DIR = Path.home() / "Desktop" / "captures"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
print(f"[setup] Captures will be saved to: {OUTPUT_DIR}")
print(f"[setup] (That folder exists: {OUTPUT_DIR.exists()})")
print()

PHOXI_CTI = (
    Path(os.environ.get("PHOXI_CONTROL_PATH", r"C:\Program Files\Photoneo\PhoXiControl"))
    / "API" / "bin" / "photoneo.cti"
)


def main() -> int:
    print(f"[Photoneo] Looking for GenTL producer at: {PHOXI_CTI}")
    print(f"[Photoneo] File exists: {PHOXI_CTI.exists()}")
    if not PHOXI_CTI.exists():
        print("ERROR: photoneo.cti not found. Check PHOXI_CONTROL_PATH env var.")
        return 1

    h = Harvester()
    h.add_file(str(PHOXI_CTI))
    h.update()

    print(f"[Photoneo] Found {len(h.device_info_list)} device(s):")
    for i, dev in enumerate(h.device_info_list):
        print(f"  [{i}] {dev}")

    if not h.device_info_list:
        print("ERROR: No Photoneo devices found. Open PhoXi Control and connect the scanner first.")
        return 1

    ia = h.create(0)
    nm = ia.remote_device.node_map

    # Print every available node so we know what we're working with
    print("\n[Photoneo] Available trigger nodes:")
    for node_name in ["TriggerMode", "TriggerSource", "TriggerSoftware", "TriggerFrame", "SendPointCloud"]:
        try:
            val = getattr(nm, node_name).value
            print(f"  {node_name} = {val}")
        except Exception as e:
            print(f"  {node_name} = <unavailable: {e}>")

    try:
        nm.TriggerMode.value = "On"
        nm.TriggerSource.value = "Software"
        print("[Photoneo] Set TriggerMode=On, TriggerSource=Software")
    except Exception as e:
        print(f"[Photoneo] Trigger setup warning: {e}")

    try:
        nm.SendPointCloud.value = True
        print("[Photoneo] Set SendPointCloud=True")
    except Exception as e:
        print(f"[Photoneo] SendPointCloud not available: {e}")

    ia.start()
    print("[Photoneo] Acquisition started\n")

    print("=" * 60)
    print("  Press [SPACE] to capture, [Q] or [ESC] to quit")
    print("=" * 60)
    print()

    capture_count = 0
    last_trigger = 0.0

    try:
        while True:
            if keyboard.is_pressed("q") or keyboard.is_pressed("esc"):
                print("Quit requested.")
                break

            if keyboard.is_pressed("space"):
                if time.monotonic() - last_trigger < 0.5:
                    time.sleep(0.05)
                    continue
                last_trigger = time.monotonic()
                capture_count += 1

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                ply_path = OUTPUT_DIR / f"capture_{capture_count:04d}_{ts}.ply"
                print(f"\n--- Capture #{capture_count} ---")
                print(f"[step 1] Will save to: {ply_path}")

                try:
                    print("[step 2] Firing software trigger...")
                    try:
                        nm.TriggerSoftware.execute()
                        print("[step 2] TriggerSoftware.execute() succeeded")
                    except Exception as e1:
                        print(f"[step 2] TriggerSoftware failed ({e1}), trying TriggerFrame...")
                        nm.TriggerFrame.execute()
                        print("[step 2] TriggerFrame.execute() succeeded")

                    print("[step 3] Fetching buffer (timeout 30s)...")
                    with ia.fetch(timeout=30) as buf:
                        print(f"[step 3] Got buffer with {len(buf.payload.components)} component(s)")
                        for ci, comp in enumerate(buf.payload.components):
                            print(f"  component[{ci}]: shape=({comp.height}x{comp.width}), "
                                  f"data_format={comp.data_format if hasattr(comp, 'data_format') else '?'}, "
                                  f"data.shape={comp.data.shape}, dtype={comp.data.dtype}")

                        comp = buf.payload.components[0]
                        h_, w_ = comp.height, comp.width
                        raw = comp.data
                        print(f"[step 4] Raw data: {raw.size} elements, dtype={raw.dtype}")
                        print(f"[step 4] Expected for HxWx3 float32: {h_*w_*3}")

                        if raw.size == h_ * w_ * 3:
                            arr = raw.reshape(h_, w_, 3).astype(np.float32, copy=True)
                            mask = ~np.all(arr == 0, axis=2)
                            xyz = arr[mask]
                            print(f"[step 4] Reshape OK -> {xyz.shape[0]} valid points "
                                  f"(out of {h_*w_} total)")
                        else:
                            print(f"[step 4] UNEXPECTED size. Trying flat reshape...")
                            xyz = raw.reshape(-1, 3).astype(np.float32, copy=True)
                            print(f"[step 4] Got {xyz.shape[0]} points")

                    print(f"[step 5] Writing PLY file...")
                    n = xyz.shape[0]
                    header = (
                        "ply\n"
                        "format binary_little_endian 1.0\n"
                        f"element vertex {n}\n"
                        "property float x\n"
                        "property float y\n"
                        "property float z\n"
                        "end_header\n"
                    ).encode("ascii")
                    with open(ply_path, "wb") as f:
                        f.write(header)
                        f.write(xyz.astype("<f4").tobytes())

                    if ply_path.exists():
                        size = ply_path.stat().st_size
                        print(f"[step 5] OK - file written: {ply_path} ({size:,} bytes)")
                    else:
                        print(f"[step 5] ERROR: file does not exist after write!")

                except Exception as e:
                    print(f"\n!!! CAPTURE FAILED: {e}")
                    print("Full traceback:")
                    traceback.print_exc()

                while keyboard.is_pressed("space"):
                    time.sleep(0.02)

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nInterrupted.")

    finally:
        print("\nShutting down...")
        try:
            ia.stop()
            ia.destroy()
        except Exception:
            pass
        try:
            h.reset()
        except Exception:
            pass

    print(f"\nDone. {capture_count} captures attempted.")
    print(f"Check folder: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())