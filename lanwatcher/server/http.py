"""Local-only HTTP server: static UI + JSON API + Server-Sent Events.

Security posture:
- binds 127.0.0.1 only (never exposed to the network)
- per-run bearer token required on every API call (window launch carries it)
- strict same-origin checks, CSP headers, no directory traversal
- no telemetry, no third-party requests of any kind
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("lanwatcher.server")

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
API_PREFIX = "/api/"


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: str = "", why: str = "", fix: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
        # Error format: What happened / Why / What to do (surfaced in UI)
        self.why = why or detail
        self.fix = fix


class RequestContext:
    def __init__(self, query: dict, body: dict, params: dict):
        self.query = query
        self.body = body
        self.params = params


class LanWatcherServer:
    def __init__(self, api, token: str, host: str = "127.0.0.1", port: int = 0, auto_token: bool = False):
        self.api = api
        self.token = token
        self.host = host
        self.port = port
        self.auto_token = auto_token  # bootstrap: redirect "/" -> "/?token=..." (demo/preview)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._sse_queues: list = []
        self._sse_lock = threading.Lock()
        self.origin = f"http://{host}"

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> int:
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.origin = f"http://{self.host}:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="lanwatcher-http", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=3.0)

    def broadcast(self, topic: str, payload) -> None:
        """Fan an event out to SSE clients (called from the event bus)."""
        msg = json.dumps({"topic": topic, "payload": payload}, default=str)
        with self._sse_lock:
            queues = list(self._sse_queues)
        for q in queues:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    # ---------------------------------------------------------------- handler

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "LANWatcher"

            def log_message(self, fmt, *args):  # quiet by default
                log.debug("%s " + fmt, self.address_string(), *args)

            # -------- helpers
            def _send(self, status: int, content_type: str, body: bytes, extra: Optional[dict] = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
                )
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _json(self, status: int, data) -> None:
                self._send(status, "application/json; charset=utf-8", json.dumps(data, default=str).encode())

            def _error(self, err: ApiError) -> None:
                self._json(
                    err.status,
                    {
                        "error": {
                            "what": err.message,
                            "why": err.why or err.detail or "See application logs for details.",
                            "fix": err.fix or "Check Settings and the collector status on the Settings page.",
                            "status": err.status,
                        }
                    },
                )

            def _auth_ok(self) -> bool:
                auth = self.headers.get("Authorization", "")
                if auth == f"Bearer {server.token}":
                    return True
                if self.headers.get("X-LANWatcher-Token") == server.token:
                    return True
                parsed = urlparse(self.path)
                if "token" in parse_qs(parsed.query) and parse_qs(parsed.query)["token"][0] == server.token:
                    return True
                return False

            def _origin_ok(self) -> bool:
                origin = self.headers.get("Origin")
                if not origin:
                    return True  # desktop webview / curl
                # same-origin: Origin host must match the request Host header
                # (also correct behind a port-forwarding preview proxy)
                if self.headers.get("Host") and urlparse(origin).netloc == self.headers.get("Host"):
                    return True
                return origin.rstrip("/") == server.origin.rstrip("/") or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")

            def _read_body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2_000_000:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8", errors="replace") or "{}")
                except ValueError:
                    return {}

            # -------- routing
            def do_GET(self):  # noqa: N802
                if not self._origin_ok():
                    return self._error(ApiError(403, "Cross-origin request rejected"))
                parsed = urlparse(self.path)
                path = parsed.path
                if path.startswith(API_PREFIX):
                    if not self._auth_ok():
                        return self._error(ApiError(401, "Missing or invalid token"))
                    if path == "/api/events":
                        return self._sse()
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if k != "token"}
                    if path == "/api/report/file":
                        return self._report_file(query)
                    return self._dispatch("GET", path, query, {})
                return self._static(path)

            def do_POST(self):  # noqa: N802
                self._mutate("POST")

            def do_PATCH(self):  # noqa: N802
                self._mutate("PATCH")

            def do_DELETE(self):  # noqa: N802
                self._mutate("DELETE")

            def _mutate(self, method: str) -> None:
                if not self._origin_ok():
                    return self._error(ApiError(403, "Cross-origin request rejected"))
                parsed = urlparse(self.path)
                if not parsed.path.startswith(API_PREFIX):
                    return self._error(ApiError(404, "Not found"))
                if not self._auth_ok():
                    return self._error(ApiError(401, "Missing or invalid token"))
                query = {k: v[0] for k, v in parse_qs(parsed.query).items() if k != "token"}
                return self._dispatch(method, parsed.path, query, self._read_body())

            def _dispatch(self, method: str, path: str, query: dict, body: dict) -> None:
                try:
                    status, data = server.api.dispatch(method, path, query, body)
                    self._json(status, data)
                except ApiError as err:
                    self._error(err)
                except Exception as exc:  # noqa: BLE001
                    log.exception("API failure on %s %s", method, path)
                    self._error(
                        ApiError(500, "Unexpected server error", f"{type(exc).__name__}: {exc}")
                    )

            def _report_file(self, query: dict) -> None:
                try:
                    body, mime, name = server.api.dispatch_report(query.get("format", "html"), query.get("preset", "7d"))
                    self._send(200, mime, body, {"Content-Disposition": f'attachment; filename="{name}"'})
                except ApiError as err:
                    self._error(err)
                except Exception as exc:  # noqa: BLE001
                    log.exception("report generation failed")
                    self._error(ApiError(500, "Report generation failed", f"{type(exc).__name__}: {exc}"))

            # -------- SSE
            def _sse(self) -> None:
                q: queue.Queue = queue.Queue(maxsize=500)
                with server._sse_lock:
                    server._sse_queues.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15.0)
                            self.wfile.write(f"data: {msg}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with server._sse_lock:
                        if q in server._sse_queues:
                            server._sse_queues.remove(q)

            # -------- static files
            def _static(self, path: str) -> None:
                if path == "/" and server.auto_token and "token=" not in self.path:
                    self.send_response(302)
                    self.send_header("Location", f"/?token={server.token}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path in ("/", ""):
                    path = "/index.html"
                rel = path.lstrip("/")
                target = (WEB_ROOT / rel).resolve()
                try:
                    target.relative_to(WEB_ROOT.resolve())
                except ValueError:
                    return self._error(ApiError(403, "Path traversal rejected"))
                if not target.is_file():
                    target = WEB_ROOT / "index.html"  # SPA fallback
                    if not target.is_file():
                        return self._error(ApiError(404, "UI file missing"))
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                self._send(200, ctype, target.read_bytes())

        return Handler
