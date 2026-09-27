"""Frozen-app launcher (PyInstaller entry).

Kept separate from lanwatcher/__main__.py so the console-less exe starts the
desktop window by default, with logging to the app data directory.
"""

import sys


def _main() -> int:
    import logging
    from pathlib import Path

    from lanwatcher import __version__
    from lanwatcher.app import create_runtime
    from lanwatcher.util import data_home

    home = data_home()
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_dir / "lanwatcher.log"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("lanwatcher")
    log.info("LAN Watcher %s starting (frozen=%s)", __version__, getattr(sys, "frozen", False))

    # delegate to the full CLI (window unless --serve/--no-window)
    from lanwatcher.__main__ import main

    argv = sys.argv[1:]
    return main(argv)


if __name__ == "__main__":
    sys.exit(_main())
