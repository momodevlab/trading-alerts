"""
start_all.py — Launch futures + stock monitors in one terminal

Starts both alert monitors as subprocesses so a single terminal window
runs everything. Ctrl+C stops both.

Usage:
    python scripts/start_all.py
    python scripts/start_all.py --with-refresh   # also start dashboard refresh loop
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


def run() -> None:
    parser = argparse.ArgumentParser(description="Start all alert monitors")
    parser.add_argument('--with-refresh', action='store_true',
                        help='Also start the dashboard refresh loop')
    args = parser.parse_args()

    python = sys.executable
    procs  = []

    print("=" * 50)
    print("  Trading Research System — Starting All")
    print("=" * 50)

    # Futures monitor
    print("\n[start_all] Starting futures alert monitor...")
    futures_proc = subprocess.Popen(
        [python, str(BASE_DIR / "alerts" / "futures_alerts.py")],
        cwd=str(BASE_DIR),
    )
    procs.append(('futures_alerts', futures_proc))
    print(f"  PID {futures_proc.pid}")

    time.sleep(2)

    # Stock monitor
    print("\n[start_all] Starting stock alert monitor...")
    stock_proc = subprocess.Popen(
        [python, str(BASE_DIR / "alerts" / "stock_alerts.py")],
        cwd=str(BASE_DIR),
    )
    procs.append(('stock_alerts', stock_proc))
    print(f"  PID {stock_proc.pid}")

    # Optional: dashboard refresh
    if args.with_refresh:
        time.sleep(1)
        print("\n[start_all] Starting dashboard refresh loop...")
        refresh_proc = subprocess.Popen(
            [python, str(BASE_DIR / "scripts" / "refresh_dashboard.py"), "--loop"],
            cwd=str(BASE_DIR),
        )
        procs.append(('refresh_dashboard', refresh_proc))
        print(f"  PID {refresh_proc.pid}")

    print("\n" + "=" * 50)
    print("  All monitors running. Press Ctrl+C to stop all.")
    print("=" * 50)
    print()

    def _stop_all(sig, frame):
        print("\n[start_all] Stopping all monitors...")
        for name, p in procs:
            try:
                p.terminate()
                print(f"  Stopped {name} (PID {p.pid})")
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _stop_all)
    signal.signal(signal.SIGTERM, _stop_all)

    # Monitor children — restart if one crashes
    while True:
        time.sleep(30)
        for i, (name, p) in enumerate(procs):
            ret = p.poll()
            if ret is not None:
                print(f"[start_all] {name} exited (code {ret}) — restarting...")
                script_map = {
                    'futures_alerts':    BASE_DIR / "alerts"   / "futures_alerts.py",
                    'stock_alerts':      BASE_DIR / "alerts"   / "stock_alerts.py",
                    'refresh_dashboard': BASE_DIR / "scripts"  / "refresh_dashboard.py",
                }
                script = script_map.get(name)
                if script:
                    extra_args = ['--loop'] if name == 'refresh_dashboard' else []
                    new_proc = subprocess.Popen(
                        [python, str(script)] + extra_args,
                        cwd=str(BASE_DIR),
                    )
                    procs[i] = (name, new_proc)
                    print(f"  Restarted {name} (PID {new_proc.pid})")


if __name__ == "__main__":
    run()
