"""Desktop window shell (pywebview -> WebView2/Edge on Windows 10/11).

Thin wrapper: the UI is served by the local-only API server. If the windowing
subsystem is unavailable (headless machine, missing WebView2 runtime), the app
gracefully falls back to headless/server mode and prints the local URL.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger("lanwatcher.desktop")


def open_window(url: str, title: str = "LAN Watcher", width: int = 1360, height: int = 860) -> bool:
    """Open the desktop window. Returns False if no GUI is available."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed - falling back to browser mode")
        return False

    try:
        window = webview.create_window(
            title,
            url,
            width=width,
            height=height,
            min_size=(980, 640),
            background_color="#0a0f1a",
            text_select=True,
        )

        def _watch() -> None:
            # keep process alive until the window closes
            pass

        threading.Thread(target=_watch, daemon=True).start()
        webview.start(debug=False)
        return True
    except Exception as exc:  # noqa: BLE001 - GUI stack varies per machine
        log.warning("desktop window unavailable (%s) - falling back to browser/server mode", exc)
        return False
