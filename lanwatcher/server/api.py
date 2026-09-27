"""REST API facade: validates requests, shapes JSON for the UI.

Error contract: {"error": {"what": ..., "why": ..., "fix": ..., "status": ...}}
matching the 'What happened -> Why -> What to do' requirement.
"""

from __future__ import annotations

import logging
from typing import Optional

from .http import ApiError

log = logging.getLogger("lanwatcher.api")


class Api:
    """Wired to the Runtime in app.py (see create_runtime)."""

    def __init__(self, runtime):
        self.rt = runtime

    # ---------------------------------------------------------------- dispatch

    def dispatch(self, method: str, path: str, query: dict, body: dict) -> tuple:
        parts = [p for p in path.split("/") if p][1:]  # drop 'api'
        try:
            if method == "GET":
                return 200, self._get(parts, query)
            if method == "POST":
                return self._post(parts, query, body)
            if method == "PATCH":
                return 200, self._patch(parts, query, body)
            if method == "DELETE":
                return 200, self._delete(parts, query)
        except ApiError:
            raise
        except KeyError as exc:
            raise ApiError(404, f"Not found: {exc}") from exc
        raise ApiError(405, f"Method {method} not allowed")

    # ---------------------------------------------------------------- GET

    def _get(self, parts: list, query: dict) -> dict:
        rt = self.rt
        head = parts[0] if parts else ""

        if head == "summary":
            return rt.summary()
        if head == "devices":
            if len(parts) > 1:
                return rt.device_detail(parts[1])
            return rt.devices(
                search=query.get("search", ""),
                type_=query.get("type", ""),
                status=query.get("status", ""),
                sort=query.get("sort", "last_seen"),
                order=query.get("order", "desc"),
                category=query.get("category", ""),
                trust=query.get("trust", ""),
            )
        if head == "history":
            return rt.history(
                device_id=query.get("device"),
                preset=query.get("preset", "24h"),
                custom_from=query.get("from"),
                custom_to=query.get("to"),
            )
        if head == "alerts":
            return rt.alerts(state=query.get("state"), kind=query.get("kind"), limit=int(query.get("limit", 500)))
        if head == "router":
            return rt.router()
        if head == "interfaces":
            return rt.interfaces()
        if head == "wifi":
            return rt.wifi()
        if head == "traffic":
            return rt.traffic(n=int(query.get("n", 120)))
        if head == "health":
            return rt.health()
        if head == "topology":
            return rt.topology()
        if head == "settings":
            return rt.get_settings()
        if head == "system":
            return rt.system()
        if head == "search":
            return rt.search(query.get("q", ""))
        if head == "reports":
            raise ApiError(400, "Use /api/reports/<fmt> via download endpoint", "reports are binary/text files", "call /api/report?format=html|json|csv|pdf")
        if head == "report":
            return rt.report_meta(query.get("format", "html"), query.get("preset", "7d"))
        raise ApiError(404, f"Unknown endpoint: /{'/'.join(parts)}", "The UI requested an endpoint this build does not provide.", "Update/restart LAN Watcher.")

    def _get_timeline(self, device_id: str, query: dict) -> dict:
        return self.rt.timeline(device_id, query.get("preset", "24h"), query.get("from"), query.get("to"))

    # ---------------------------------------------------------------- POST

    def _post(self, parts: list, query: dict, body: dict) -> tuple:
        rt = self.rt
        head = parts[0] if parts else ""
        if head == "alerts" and len(parts) > 2 and parts[2] == "triage":
            state = body.get("state")
            alert = rt.triage_alert(int(parts[1]), state)
            return 200, {"alert": alert}
        if head == "scan":
            return 202, {"started": rt.trigger_scan()}
        if head == "tools":
            action = parts[1] if len(parts) > 1 else ""
            return 200, rt.tool(action, body)
        if head == "settings" and len(parts) > 1 and parts[1] == "test-router":
            return 200, rt.test_router()
        if head == "devices" and len(parts) > 2 and parts[2] == "tags":
            rt.add_tag(parts[1], body.get("tag", ""))
            return 201, {"ok": True}
        raise ApiError(404, f"Unknown endpoint: POST /{'/'.join(parts)}")

    # ---------------------------------------------------------------- PATCH

    def _patch(self, parts: list, query: dict, body: dict) -> dict:
        if parts and parts[0] == "devices" and len(parts) > 1:
            return self.rt.update_device(parts[1], body)
        if parts and parts[0] == "settings":
            return self.rt.update_settings(body)
        raise ApiError(404, f"Unknown endpoint: PATCH /{'/'.join(parts)}")

    # ---------------------------------------------------------------- DELETE

    def _delete(self, parts: list, query: dict) -> dict:
        if parts and parts[0] == "devices" and len(parts) == 2:
            return self.rt.delete_device(parts[1])
        if parts and parts[0] == "devices" and len(parts) == 4 and parts[2] == "tags":
            self.rt.remove_tag(parts[1], parts[3])
            return {"ok": True}
        raise ApiError(404, f"Unknown endpoint: DELETE /{'/'.join(parts)}")

    # ---------------------------------------------------------------- raw report bytes handled separately
    def dispatch_report(self, fmt: str, preset: str) -> tuple:
        return self.rt.report_bytes(fmt, preset)
