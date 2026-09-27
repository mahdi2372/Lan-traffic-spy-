"""LAN Watcher entry point.

Usage:
  python -m lanwatcher                # desktop window (falls back to browser)
  python -m lanwatcher --serve        # headless server (prints local URL)
  python -m lanwatcher --demo         # simulated network data (demo/testing)
  python -m lanwatcher --port 8765    # fixed port (default: ephemeral)
  python -m lanwatcher --no-window    # server mode without opening a window
  python -m lanwatcher --open-browser # open the system browser with the UI
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import webbrowser

from . import APP_NAME, __version__
from .app import create_runtime


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lanwatcher", description=f"{APP_NAME} - privacy-preserving LAN monitoring")
    parser.add_argument("--serve", action="store_true", help="run headless (no desktop window)")
    parser.add_argument("--no-window", action="store_true", help="do not open the desktop window")
    parser.add_argument("--demo", action="store_true", help="use simulated network data (for testing/demo)")
    parser.add_argument("--port", type=int, default=0, help="local HTTP port (default: ephemeral)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1, loopback only)")
    parser.add_argument("--open-browser", action="store_true", help="open the UI in the default browser")
    parser.add_argument("--auto-token", action="store_true", help="redirect / to /?token=... (preview/demo convenience)")
    parser.add_argument("--home", default=None, help="data directory (default: per-user app data dir)")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("lanwatcher")

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("Binding to %s exposes the UI beyond this machine - LAN Watcher is designed for loopback only.", args.host)

    runtime = create_runtime(home=args.home, demo=args.demo)
    if args.port:
        runtime.server.port = args.port
    runtime.server.host = args.host
    runtime.server.auto_token = args.auto_token
    runtime.start()
    url = runtime.url
    print(f"\n  {APP_NAME} v{__version__}" + ("  [DEMO DATA]" if args.demo else ""))
    print(f"  Local UI: {url}")
    print("  Local-only: nothing leaves this machine. Press Ctrl+C to exit.\n")

    stop_event = threading.Event()

    def _sig(_s, _f):
        stop_event.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    opened = False
    if not args.serve and not args.no_window:
        from .desktop.window import open_window

        opened = open_window(url, title=f"{APP_NAME}" + (" (demo)" if args.demo else ""))
    if args.open_browser or (not opened and not args.serve):
        if not args.serve:
            webbrowser.open(url)

    try:
        while not stop_event.wait(0.5):
            if not runtime.discovery.running and not args.demo:
                log.warning("discovery worker stopped unexpectedly - restarting")
                runtime.discovery.start()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down…")
        runtime.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
