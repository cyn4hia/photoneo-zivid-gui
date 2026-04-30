"""
capture both zivid and photoneo - full trace version
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import queue
import threading
import msvcrt
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


def _save_zivid_ply(frame: zivid.Frame, ply_path: Path) -> None:
    point_cloud = frame.point_cloud()
    for method_name in ("save", "save_ply"):
        if hasattr(point_cloud, method_name):
            getattr(point_cloud, method_name)(str(ply_path))
            return
    frame.save(str(ply_path))


class ZividCapture:
    def __init__(self, settings_yaml: str | None = None) -> None:
        print(f"[Zivid] zivid version: {zivid.__version__}", flush=True)
        self.app = zivid.Application()
        cams = self.app.cameras()
        print(f"[Zivid] Found {len(cams)} camera(s)", flush=True)
        if not cams:
            raise RuntimeError("No Zivid cameras detected.")
        self.camera = self.app.connect_camera()
        if settings_yaml and Path(settings_yaml).exists():
            self.settings = zivid.Settings.load(settings_yaml)
        else:
            self.settings = zivid.Settings(
                acquisitions=[zivid.Settings.Acquisition()],
                color=zivid.Settings2D(acquisitions=[zivid.Settings2D.Acquisition()]),
            )
        info = self.camera.info
        print(f"[Zivid] Connected: {info.model_name} (SN={info.serial_number})", flush=True)

    def capture(self, ply_path: Path) -> bool:
        try:
            with self.camera.capture_2d_3d(self.settings) as frame:
                _save_zivid_ply(frame, ply_path)
            if ply_path.exists():
                print(f"[Zivid]    -> {ply_path.name} ({ply_path.stat().st_size:,} bytes)", flush=True)
                return True
            return False
        except Exception as e:
            print(f"[Zivid]    !! capture failed: {e}", flush=True)
            traceback.print_exc()
            return False

    def close(self) -> None:
        try:
            self.camera.disconnect()
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
        self.ia = self.h.create(0)
        nm = self.ia.remote_device.node_map
        try:
            nm.TriggerMode.value = "On"
            nm.TriggerSource.value = "Software"
        except Exception:
            pass
        self.ia.start()
        print("[Photoneo] Acquisition started", flush=True)

    def trigger(self) -> bool:
        nm = self.ia.remote_device.node_map
        try:
            try:
                nm.TriggerSoftware.execute()
            except Exception:
                nm.TriggerFrame.execute()
            print("[Photoneo] -> trigger fired", flush=True)
            return True
        except Exception as e:
            print(f"[Photoneo] !! trigger failed: {e}", flush=True)
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
    print(f"[trace] main() start", flush=True)
    print(f"Output dir for Zivid: {OUTPUT_DIR}", flush=True)

    zv = None
    pn = None

    try:
        zv = ZividCapture(ZIVID_SETTINGS_YAML)
    except Exception as e:
        print(f"[Zivid] FAILED: {e}", flush=True)
        traceback.print_exc()

    try:
        pn = PhotoneoTrigger(PHOXI_CTI)
    except Exception as e:
        print(f"[Photoneo] FAILED: {e}", flush=True)
        traceback.print_exc()

    if zv is None and pn is None:
        print("Both cameras failed.", flush=True)
        return 1

    capture_count = 0
    key_queue = queue.Queue()
    stop_reader = threading.Event()

    def key_reader():
        print("[trace] reader thread alive", flush=True)
        while not stop_reader.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    print(f"[trace] reader got: {repr(ch)}", flush=True)
                    key_queue.put(ch)
            except Exception as e:
                print(f"[trace] reader exception: {e}", flush=True)
            time.sleep(0.05)
        print("[trace] reader thread exiting", flush=True)

    threading.Thread(target=key_reader, daemon=True).start()

    print("\n" + "=" * 60, flush=True)
    print("Press SPACE or ENTER to capture. Press Q to quit.", flush=True)
    print("=" * 60 + "\n", flush=True)

    try:
        while True:
            print(f"[trace] LOOP TOP, count={capture_count}", flush=True)
            try:
                ch = key_queue.get(timeout=300)
            except queue.Empty:
                print("[trace] 5min idle, still waiting...", flush=True)
                continue

            print(f"[trace] main got: {repr(ch)}", flush=True)

            if ch.lower() == "q" or ch == "\x1b":
                print("[trace] quit", flush=True)
                stop_reader.set()
                break

            if ch not in (" ", "\r", "\n"):
                print(f"[trace] ignoring {repr(ch)}", flush=True)
                continue

            capture_count += 1
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = f"capture_{capture_count:04d}_{ts}"
            print(f"\n--- Capture #{capture_count} ({ts}) ---", flush=True)
            t0 = time.monotonic()

            try:
                print("[trace] calling Zivid", flush=True)
                if zv is not None:
                    zv.capture(OUTPUT_DIR / f"{stem}_zivid.ply")
                print("[trace] calling Photoneo", flush=True)
                if pn is not None:
                    pn.trigger()
                print("[trace] capture block done", flush=True)
            except Exception as e:
                print(f"[trace] CAPTURE EXCEPTION: {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()

            dt = time.monotonic() - t0
            print(f"--- done in {dt:.1f}s ---", flush=True)
            print("[trace] returning to LOOP TOP\n", flush=True)
    except Exception as e:
        print(f"[trace] LOOP EXCEPTION: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
    finally:
        print("[trace] entering finally block", flush=True)
        if zv is not None:
            zv.close()
        if pn is not None:
            pn.close()
        print(f"[trace] main() returning, total captures = {capture_count}", flush=True)

    return 0


if __name__ == "__main__":
    rc = main()
    print(f"[trace] sys.exit({rc})", flush=True)
    sys.exit(rc)