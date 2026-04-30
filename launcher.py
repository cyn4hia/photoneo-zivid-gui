"""
launcher for hotkey
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import keyboard


WORKER_SCRIPT = Path(__file__).parent / "capture_worker.py"
PYTHON_EXE = sys.executable 


def main() -> int:
    if not WORKER_SCRIPT.exists():
        print(f"ERROR: worker script not found at {WORKER_SCRIPT}")
        return 1

    print(f"Launcher PID: {os.getpid()}")
    print(f"Worker script: {WORKER_SCRIPT}")
    print(f"Python: {PYTHON_EXE}")
    print()
    print("=" * 60)
    print("  [SPACE]      capture")
    print("  [Q] / [ESC]  quit")
    print("=" * 60)
    print()

    capture_count = 0
    last_trigger = 0.0
    DEBOUNCE_SEC = 0.3

    print("Listening for hotkeys...")

    try:
        while True:
            time.sleep(0.05)

            if keyboard.is_pressed("q") or keyboard.is_pressed("esc"):
                print("Quit requested.")
                break

            if keyboard.is_pressed("space"):
                if time.monotonic() - last_trigger < DEBOUNCE_SEC:
                    continue
                last_trigger = time.monotonic()

                capture_count += 1
                idx = f"{capture_count:04d}"
                print(f"\n>>> Capture #{capture_count} - spawning worker...")
                t0 = time.monotonic()

                # Run the worker as a child process. If it crashes, hangs,
                # gets killed - we don't care, we just move on.
                try:
                    result = subprocess.run(
                        [PYTHON_EXE, str(WORKER_SCRIPT), idx],
                        timeout=120,  # 2 min max per capture, just in case
                    )
                    print(f">>> Worker exit code: {result.returncode}")
                except subprocess.TimeoutExpired:
                    print(">>> Worker timed out (>120s) - moving on")
                except Exception as e:
                    print(f">>> Worker spawn error: {e}")

                dt = time.monotonic() - t0
                print(f">>> Capture #{capture_count} done in {dt:.1f}s\n")

                while keyboard.is_pressed("space"):
                    time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"\nTotal captures: {capture_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())