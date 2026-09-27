"""Application runtime (composition root): wires config, storage, collectors,
services, event bus and the local API server.

The desktop shell (pywebview) and headless mode both use this runtime.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from .collectors.arp import ARPCollector
from .collectors.base import CollectorContext, CollectorRegistry
from .collectors.dhcp import DHCPCollector
from .collectors.ethernet import EthernetCollector
from .collectors.flow import FlowCollector
from .collectors.health import HealthCollector
from .collectors.neighbors import NeighborCollector
from .collectors.router import RouterCollector
from .collectors.simulated import SimulatedCollector, SimulatedNetworkProvider
from .collectors.wifi import WiFiCollector
from .collectors.windows_net import WindowsNetworkCollector
from .config import Config
from .db import Database, Repositories
from .events import (
    TOPIC_ALERT_NEW,
    TOPIC_ALERT_UPDATED,
    TOPIC_DEVICE_NEW,
    TOPIC_DEVICE_OFFLINE,
    TOPIC_DEVICE_ONLINE,
    TOPIC_DEVICE_UPDATED,
    TOPIC_HEALTH,
    EventBus,
)
from .models import TRUST_STATES
from .parsing.oui import OuiLookup
from .secure import SecureStore
from .server.http import ApiError
from .services.alerts import AlertService
from .services.correlation import CorrelationEngine
from .services.discovery import DiscoveryService
from .services.history import HistoryService
from .services.metrics import db_size, process_metrics
from .services.ping import Pinger
from .services.reports import ReportService
from .services.traffic import TrafficService
from .util import CommandRunner, app_dir, is_windows, iso

log = logging.getLogger("lanwatcher.runtime")

USER_DEVICE_FIELDS = ("friendly_name", "notes", "category", "pinned", "trust_state", "connection_type")


class Runtime:
    def __init__(self, home: Optional[Path] = None, demo: bool = False, enable_server: bool = True):
        self.home = Path(home) if home else app_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.demo = demo
        self.config = Config(self.home / "config.json")
        self.db = Database(self.home / "lanwatcher.db")
        self.repos = Repositories(self.db)
        self.bus = EventBus()
        self.secure = SecureStore(self.home)
        self.oui = OuiLookup(self.home / "oui.txt")
        self.runner = CommandRunner()
        self.traffic = TrafficService()
        self.history = HistoryService(self.repos)
        self.reports = ReportService(self.repos, self.history)
        self.alerts = AlertService(self.repos, self.bus, self.config)
        self.pinger = Pinger()
        self.visible_networks: list = []
        self.last_health: dict = {}
        self._stop = threading.Event()
        self._workers: list = []
        self.token = self._load_or_create_token()
        self.ctx = self._make_context()
        self.registry = self._make_registry()
        self.discovery = DiscoveryService(self.config, self.repos, self.registry, self.bus, self.pinger)
        self.server = None
        if enable_server:
            from .server.api import Api
            from .server.http import LanWatcherServer

            self.server = LanWatcherServer(Api(_ApiFacade(self)), token=self.token)
        self._wire_events()

    # ================================================================ setup

    def _load_or_create_token(self) -> str:
        path = self.home / "api.token"
        try:
            if path.exists():
                tok = path.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except OSError:
            pass
        tok = secrets.token_urlsafe(32)
        try:
            path.write_text(tok, encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            pass
        return tok

    def _make_context(self) -> CollectorContext:
        import sys

        ctx = CollectorContext(
            config=self.config,
            runner=self.runner,
            oui=self.oui,
            bus=self.bus,
            is_windows=is_windows(),
            is_macos=sys.platform == "darwin",
            is_elevated=_is_elevated(),
            data_dir=self.home,
            state={},
        )
        ctx.secret_store = self.secure
        return ctx

    def _make_registry(self) -> CollectorRegistry:
        ctx = self.ctx
        collectors = []
        if self.demo:
            collectors.append(SimulatedCollector(ctx, provider=SimulatedNetworkProvider()))
        else:
            collectors.extend(
                [
                    WindowsNetworkCollector(ctx),
                    ARPCollector(ctx),
                    NeighborCollector(ctx),
                    WiFiCollector(ctx),
                    EthernetCollector(ctx),
                    DHCPCollector(ctx),
                    RouterCollector(ctx),
                    FlowCollector(ctx),
                ]
            )
        collectors.append(HealthCollector(ctx))
        return CollectorRegistry(collectors, bus=self.bus)

    def _wire_events(self) -> None:
        def on_event(ev):
            topic, payload = ev.topic, ev.payload
            try:
                if topic == TOPIC_DEVICE_NEW:
                    alert = self.alerts.on_new_device(payload.get("device_id"))
                    if alert:
                        log.info("new device alert: %s", alert.message)
                elif topic == TOPIC_DEVICE_ONLINE:
                    self.alerts.on_device_event("online", payload.get("device_id"), payload)
                elif topic == TOPIC_DEVICE_OFFLINE:
                    self.alerts.on_device_event("offline", payload.get("device_id"), payload)
                elif topic == "scan.finished":
                    snap = self.discovery.correlator  # noqa
                elif topic == TOPIC_HEALTH:
                    self.last_health = payload
                    self.repos.settings.set("last_health", __import__("json").dumps(payload, default=str))
                    self.alerts.on_health(payload)
            except Exception:  # noqa: BLE001
                log.exception("event wiring failure for %s", topic)

        self.bus.subscribe("*", on_event)
        if self.server is not None:
            self.bus.subscribe("*", lambda ev: self.server.broadcast(ev.topic, ev.payload) if self.server else None)

        # ip_change events -> alerts (from stored correlation events)
        self.bus.subscribe(TOPIC_DEVICE_UPDATED, self._check_change_alerts)

    def _check_change_alerts(self, ev) -> None:
        # correlation stores change events directly; alert on newest ip_change
        device_id = (ev.payload or {}).get("device_id")
        if not device_id:
            return
        rows = self.repos.events.list(device_id=device_id, event_type="ip_change", limit=1)
        if rows:
            self.alerts.on_device_event("ip_change", device_id, rows[0].get("details") or {})

    # ================================================================ lifecycle

    def start(self) -> None:
        self.discovery.start()
        self._workers.append(threading.Thread(target=self._bandwidth_loop, name="lanwatcher-bandwidth", daemon=True))
        self._workers.append(threading.Thread(target=self._retention_loop, name="lanwatcher-retention", daemon=True))
        for w in self._workers:
            w.start()
        if self.server is not None:
            port = self.server.start()
            log.info("LAN Watcher API listening on http://127.0.0.1:%s (local only)", port)

    def stop(self) -> None:
        self._stop.set()
        self.discovery.stop()
        if self.server is not None:
            self.server.stop()
        for w in self._workers:
            w.join(timeout=3.0)
        self.db.close()

    def _bandwidth_loop(self) -> None:
        interval = int(self.config.get("traffic.interval_s", 5))
        while not self._stop.wait(interval):
            try:
                sample = self.traffic.sample()
                if sample:
                    self.bus.publish(
                        "stats.bandwidth",
                        sample.to_dict(),
                    )
                    # self-device byte accounting from interface counters (measured, not invented)
                    self_device = self.repos.devices.find_self()
                    if self_device:
                        self.repos.devices.update_fields(
                            self_device["device_id"],
                            {"bytes_sent": sample.bytes_sent_total, "bytes_recv": sample.bytes_recv_total},
                        )
            except Exception:  # noqa: BLE001
                log.exception("bandwidth sampling failed")

    def _retention_loop(self) -> None:
        last_day = None
        while not self._stop.wait(60):
            try:
                day = iso()[:10]
                if day != last_day:
                    last_day = day
                    r = self.config.get
                    self.repos.retention.purge(
                        flows_days=r("retention.flows_days"),
                        events_days=r("retention.events_days"),
                        sessions_days=r("retention.sessions_days"),
                        alerts_days=r("retention.alerts_days"),
                        vacuum=r("retention.vacuum_on_purge"),
                    )
            except Exception:  # noqa: BLE001
                log.exception("retention job failed")

    @property
    def url(self) -> str:
        if self.server is None:
            return ""
        return f"{self.server.origin}/?token={self.token}"


# ====================================================================== API impl


class _ApiFacade:
    """Implements the REST surface used by server.api.Api."""

    def __init__(self, rt: Runtime):
        self.rt = rt

    # -------- read

    def summary(self) -> dict:
        rt = self.rt
        counts = rt.repos.devices.counts()
        bw = rt.traffic.latest()
        new_today = [a for a in rt.repos.alerts.list(kind="new_device", limit=50) if a["ts"] >= iso()[:10]]
        alerts_counts = rt.repos.alerts.counts_by_state()
        health = rt.last_health or {}
        return {
            "counts": counts,
            "bandwidth": bw.to_dict() if bw else {"up_bps": 0, "down_bps": 0, "total_bps": 0, "bytes_sent_total": 0, "bytes_recv_total": 0},
            "new_devices_today": len(new_today),
            "alerts": {"total": sum(alerts_counts.values()), "by_state": alerts_counts, "open": alerts_counts.get("unknown", 0)},
            "health": health,
            "scan": {"count": rt.discovery.scan_count, "last_ms": rt.discovery.last_scan_ms, "running": rt.discovery.running},
        }

    def devices(self, search: str = "", type_: str = "", status: str = "", sort: str = "last_seen", order: str = "desc", category: str = "", trust: str = "") -> dict:
        rows = self.rt.repos.devices.search(search) if search else self.rt.repos.devices.list_all()
        if type_ in ("wifi", "ethernet", "unknown"):
            rows = [r for r in rows if (r.get("connection_type") == type_ or (type_ == "unknown" and r.get("connection_type") not in ("wifi", "ethernet")))]
        if status in ("online", "offline"):
            rows = [r for r in rows if bool(r.get("online")) == (status == "online")]
        if category:
            rows = [r for r in rows if r.get("category") == category]
        if trust:
            rows = [r for r in rows if r.get("trust_state") == trust]

        def sort_key(r):
            if sort == "bandwidth":
                return (r.get("bytes_sent") or 0) + (r.get("bytes_recv") or 0)
            if sort == "latency":
                return r.get("last_latency_ms") if r.get("last_latency_ms") is not None else 1e9
            if sort == "last_seen":
                return r.get("last_seen") or ""
            if sort == "name":
                return (r.get("friendly_name") or r.get("hostname") or r.get("ipv4") or "").lower()
            return r.get(sort) or ""

        rows = sorted(rows, key=sort_key, reverse=(order != "asc"))
        rows = sorted(rows, key=lambda r: not bool(r.get("pinned")))
        return {"devices": [self._device_row(r) for r in rows], "total": len(rows)}

    def _device_row(self, r: dict) -> dict:
        out = dict(r)
        out.pop("notes_encrypted", None)
        if out.get("notes_encrypted_flag"):
            pass
        notes = r.get("notes")
        if notes and self.rt.secure and (r.get("notes_encrypted") or 0):
            out["notes"] = self.rt.secure.unprotect(notes)
        out.pop("source_summary", None)
        loss = 0.0
        if r.get("probes_sent"):
            loss = round(100.0 * (r.get("probes_lost") or 0) / r["probes_sent"], 1)
        out["packet_loss_pct"] = loss
        return out

    def device_detail(self, device_id: str) -> dict:
        r = self.rt.repos.devices.get(device_id)
        if not r:
            raise ApiError(404, "Device not found", "The device may have been deleted.", "Refresh the device list.")
        row = self._device_row(r)
        row["observations"] = self.rt.repos.observations.current(device_id)
        row["conflicts"] = {
            k: v[:20] for k, v in self.rt.repos.observations.conflicts(device_id).items()
        }
        row["field_history"] = self.rt.repos.observations.field_history(device_id)[:200]
        row["sessions"] = self.rt.repos.sessions.list_for(device_id, limit=100)
        row["flows"] = self.rt.repos.flows.recent(limit=100, device_id=device_id)
        row["traffic_summary"] = self.rt.repos.flows.summary_for_device(device_id)
        row["tags"] = self.rt.repos.tags.for_device(device_id)
        row["timeline"] = self.rt.history.timeline(device_id=device_id, preset="30d", limit=200)
        return {"device": row}

    def history(self, device_id=None, preset="24h", custom_from=None, custom_to=None) -> dict:
        rows = self.rt.history.timeline(device_id=device_id, preset=preset, custom_from=custom_from, custom_to=custom_to)
        return {"events": rows, "preset": preset}

    def alerts(self, state=None, kind=None, limit=500) -> dict:
        rows = self.rt.repos.alerts.list(state=state, kind=kind, limit=limit)
        return {"alerts": rows, "counts": self.rt.repos.alerts.counts_by_state()}

    def router(self) -> dict:
        row = self.rt.repos.router.latest() or {}
        cfg = self.rt.config
        return {
            "router": row,
            "system": {
                "default_gateway": self.rt.ctx.state.get("system_info", {}).get("default_gateway"),
                "firewall": self.rt.ctx.state.get("system_info", {}).get("firewall"),
                "dhcp_server": self.rt.ctx.state.get("system_info", {}).get("dhcp_server"),
                "lease_obtained": self.rt.ctx.state.get("system_info", {}).get("lease_obtained"),
                "lease_expires": self.rt.ctx.state.get("system_info", {}).get("lease_expires"),
            },
            "integration": {
                "upnp_enabled": cfg.get("router.upnp_enabled"),
                "api_enabled": cfg.get("router.api_enabled"),
                "base_url": cfg.get("router.base_url"),
                "auth_type": cfg.get("router.auth_type"),
            },
        }

    def interfaces(self) -> dict:
        rows = self.rt.repos.interfaces.latest()
        adapters = self.rt.ctx.state.get("system_info", {}).get("adapters", [])
        return {"interfaces": rows, "adapters": adapters}

    def wifi(self) -> dict:
        links = self.rt.repos.wifi.latest()
        return {"links": links, "visible_networks": self.rt.visible_networks or []}

    def traffic(self, n: int = 120) -> dict:
        flows = self.rt.repos.flows.recent(limit=200)
        return {
            "bandwidth": self.rt.traffic.recent(n),
            "latest": self.rt.traffic.latest().to_dict() if self.rt.traffic.latest() else None,
            "flows": flows,
            "available": self.rt.traffic.available,
            "note": "Flow metadata only. Bytes appear only where OS/router telemetry provides them.",
        }

    def health(self) -> dict:
        return self.rt.last_health or {"overall": "unknown", "score": 0, "components": []}

    def topology(self) -> dict:
        return _build_topology(self.rt)

    def get_settings(self) -> dict:
        return {
            "settings": _public_settings(self.rt.config),
            "secure_available": self.rt.secure.available(),
            "elevated": _is_elevated(),
        }

    def update_settings(self, body: dict) -> dict:
        values = body.get("values") or {}
        clean = {}
        for k, v in values.items():
            if not isinstance(k, str) or "." not in k:
                continue
            clean[k] = v
        # protect secrets if provided as plain fields
        if "router.password" in clean:
            self.rt.config.set("router.password_ref", self.rt.secure.protect(clean.pop("router.password")) or "")
        if "router.token" in clean:
            self.rt.config.set("router.token_ref", self.rt.secure.protect(clean.pop("router.token")) or "")
        self.rt.config.update(clean)
        return {"settings": _public_settings(self.rt.config)}

    def system(self) -> dict:
        statuses = [
            {
                "name": s.name,
                "ok": s.ok,
                "runs": s.runs,
                "errors": s.errors,
                "last_run": s.last_run,
                "last_error": s.last_error,
                "last_unavailable": s.last_unavailable,
                "last_duration_ms": round(s.last_duration_ms, 1),
                "detail": s.detail,
            }
            for s in self.rt.registry.statuses()
        ]
        import platform

        return {
            "metrics": process_metrics(),
            "db_size_mb": db_size(self.rt.db.path),
            "collectors": statuses,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "elevated": _is_elevated(),
            },
            "version": __import__("lanwatcher").__version__,
            "demo_mode": self.rt.demo,
        }

    def search(self, q: str) -> dict:
        rows = self.rt.repos.devices.search(q)
        return {"results": [self._device_row(r) for r in rows[:20]]}

    def report_meta(self, fmt: str, preset: str) -> dict:
        if fmt not in ("html", "json", "csv", "pdf"):
            raise ApiError(400, f"Unknown report format '{fmt}'", "Valid formats: html, json, csv, pdf.", "Choose one of the supported formats.")
        return {"format": fmt, "preset": preset, "url": f"/api/report/file?format={fmt}&preset={preset}&token=***"}

    def report_bytes(self, fmt: str, preset: str) -> tuple:
        content, mime, name = self.rt.reports.generate(fmt, preset)
        body = content.encode("utf-8") if isinstance(content, str) else content
        return body, mime, name

    # -------- write

    def update_device(self, device_id: str, body: dict) -> dict:
        r = self.rt.repos.devices.get(device_id)
        if not r:
            raise ApiError(404, "Device not found", "The device may have been deleted.", "Refresh the device list.")
        updates = {}
        for key in USER_DEVICE_FIELDS:
            if key not in body:
                continue
            value = body[key]
            if key == "trust_state" and value not in TRUST_STATES:
                raise ApiError(400, "Invalid trust state", "Allowed: trusted, unknown, ignore, investigate.", "Pick a valid triage state.")
            if key == "category" and value is not None:
                value = str(value)[:40]
            if key == "friendly_name" and value is not None:
                value = str(value)[:80]
            if key == "notes":
                if value and self.rt.config.get("privacy.encrypt_notes") and self.rt.secure.available():
                    updates["notes_encrypted"] = 1
                    value = self.rt.secure.protect(str(value))
                else:
                    updates["notes_encrypted"] = 0
                value = str(value)[:2000] if value else None
            updates[key] = value
        if updates:
            self.rt.repos.devices.update_fields(device_id, updates)
        return {"device": self._device_row(self.rt.repos.devices.get(device_id))}

    def delete_device(self, device_id: str) -> dict:
        if not self.rt.repos.devices.get(device_id):
            raise ApiError(404, "Device not found", "Already deleted.", "Refresh the device list.")
        self.rt.repos.devices.delete(device_id)
        return {"deleted": device_id}

    def triage_alert(self, alert_id: int, state: str):
        if state not in TRUST_STATES:
            raise ApiError(400, "Invalid triage state", "Allowed: trusted, unknown, ignore, investigate.", "Pick a valid state.")
        alert = self.rt.alerts.triage(alert_id, state)
        if alert is None:
            raise ApiError(404, "Alert not found", "The alert may have been removed by retention.", "Refresh alerts.")
        return alert

    def trigger_scan(self) -> int:
        threading.Thread(target=self.rt.discovery.scan_once, name="lanwatcher-manual-scan", daemon=True).start()
        return self.rt.discovery.scan_count

    def tool(self, action: str, body: dict) -> dict:
        rt = self.rt
        if action == "clear-logs":
            rt.repos.retention.clear_logs()
            return {"cleared": "network_events, traffic_flows"}
        if action == "delete-history":
            keep = body.get("keep_devices", True)
            rt.repos.retention.delete_history(keep_devices=bool(keep))
            return {"deleted": "history", "kept_devices": bool(keep)}
        if action == "purge-retention":
            r = rt.config.get
            deleted = rt.repos.retention.purge(
                flows_days=r("retention.flows_days"),
                events_days=r("retention.events_days"),
                sessions_days=r("retention.sessions_days"),
                alerts_days=r("retention.alerts_days"),
                vacuum=True,
            )
            return {"deleted": deleted}
        if action == "reset-settings":
            rt.config.reset()
            return {"settings": rt.config.as_dict()}
        raise ApiError(400, f"Unknown tool action '{action}'", "Valid: clear-logs, delete-history, purge-retention, reset-settings.", "Use a supported action.")

    def add_tag(self, device_id: str, tag: str) -> None:
        self.rt.repos.tags.add(device_id, tag[:40])

    def remove_tag(self, device_id: str, tag: str) -> None:
        self.rt.repos.tags.remove(device_id, tag)

    def test_router(self) -> dict:
        from .collectors.router import RouterCollector

        collector = RouterCollector(self.rt.ctx)
        result = collector.collect()
        if result.unavailable_reason:
            return {
                "ok": False,
                "what": "Router integration produced no data",
                "why": result.unavailable_reason,
                "fix": "Enable UPnP on the router for local device info, or configure the admin API under Settings -> Router with valid credentials.",
            }
        return {"ok": True, "fields": {k: v for k, v in (result.router.fields if result.router else {}).items() if k != "_sources"}}


def _build_topology(rt: Runtime) -> dict:
    """Honest topology: nodes/edges only where relationships are established.

    Internet -> Gateway -> (APs | wired segment | unverified segment) -> clients.
    Clients attach to the segment matching their evidence; devices whose link
    type is unknown attach to an 'Unverified links' node - we never invent
    relationships that data cannot establish.
    """
    devices = rt.repos.devices.list_all()
    router_row = rt.repos.router.latest() or {}
    gateway_ip = rt.ctx.state.get("gateway_ip") or router_row.get("gateway_ip")
    nodes = [{"id": "internet", "kind": "internet", "label": "Internet", "tier": 0}]
    edges = []
    gw_node = None
    for d in devices:
        if d.get("category") == "router" or (gateway_ip and (d.get("ipv4") == gateway_ip)):
            gw_node = {
                "id": d["device_id"],
                "kind": "router",
                "label": d.get("friendly_name") or d.get("hostname") or d.get("ipv4") or "Gateway",
                "tier": 1,
                "device_id": d["device_id"],
                "online": bool(d.get("online")),
                "vendor": d.get("vendor"),
                "ip": d.get("ipv4"),
            }
            break
    if gw_node is None and gateway_ip:
        gw_node = {"id": "gateway", "kind": "router", "label": gateway_ip, "tier": 1, "ip": gateway_ip, "online": True}
    if gw_node:
        nodes.append(gw_node)
        edges.append({"from": "internet", "to": gw_node["id"], "kind": "wan"})

    # access points (observed BSSIDs)
    aps = [d for d in devices if d.get("category") == "ap"]
    for ap in aps:
        nodes.append(
            {
                "id": ap["device_id"],
                "kind": "ap",
                "label": ap.get("friendly_name") or ap.get("mac") or "AP",
                "tier": 2,
                "device_id": ap["device_id"],
                "online": bool(ap.get("online")),
                "vendor": ap.get("vendor"),
            }
        )
        parent = gw_node["id"] if gw_node else "internet"
        # only claim AP is behind gateway - not which switch port etc.
        edges.append({"from": parent, "to": ap["device_id"], "kind": "lan-ap"})

    # segment nodes
    has_wired = any(d.get("connection_type") == "ethernet" and d.get("category") not in ("router", "ap") for d in devices)
    has_wifi = any(d.get("connection_type") == "wifi" and d.get("category") not in ("router", "ap") for d in devices)
    has_unknown = any(d.get("connection_type") not in ("wifi", "ethernet") and d.get("category") not in ("router", "ap") for d in devices)
    parent = gw_node["id"] if gw_node else "internet"
    seg_nodes = {}
    if has_wired:
        seg_nodes["wired"] = {"id": "seg-wired", "kind": "segment", "label": "Wired LAN segment", "tier": 2}
        nodes.append(seg_nodes["wired"])
        edges.append({"from": parent, "to": "seg-wired", "kind": "lan"})
    if has_wifi:
        seg_nodes["wifi"] = {"id": "seg-wifi", "kind": "segment", "label": "Wireless segment", "tier": 2}
        nodes.append(seg_nodes["wifi"])
        edges.append({"from": parent, "to": "seg-wifi", "kind": "lan"})
    if has_unknown:
        seg_nodes["unknown"] = {"id": "seg-unknown", "kind": "segment", "label": "Unverified links", "tier": 2}
        nodes.append(seg_nodes["unknown"])
        edges.append({"from": parent, "to": "seg-unknown", "kind": "lan"})

    for d in devices:
        if d.get("category") in ("router", "ap") or d.get("is_self") is None:
            pass
        if d.get("category") in ("router", "ap"):
            continue
        ct = d.get("connection_type")
        if ct == "ethernet" and "wired" in seg_nodes:
            seg = "seg-wired"
        elif ct == "wifi" and "wifi" in seg_nodes:
            seg = "seg-wifi"
        elif "unknown" in seg_nodes:
            seg = "seg-unknown"
        elif "wifi" in seg_nodes:
            seg = "seg-wifi"
        elif "wired" in seg_nodes:
            seg = "seg-wired"
        else:
            seg = parent
        nodes.append(
            {
                "id": d["device_id"],
                "kind": "self" if d.get("is_self") else ("client-" + (ct or "unknown")),
                "label": d.get("friendly_name") or d.get("hostname") or d.get("ipv4") or d.get("mac") or d["device_id"],
                "tier": 3,
                "device_id": d["device_id"],
                "online": bool(d.get("online")),
                "vendor": d.get("vendor"),
                "ip": d.get("ipv4"),
            }
        )
        edges.append({"from": seg, "to": d["device_id"], "kind": "client"})
    return {"nodes": nodes, "edges": edges, "gateway_ip": gateway_ip}


def _is_elevated() -> bool:
    try:
        import ctypes

        if is_windows():
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        import os

        return os.geteuid() == 0
    except Exception:  # noqa: BLE001
        return False


def _public_settings(config) -> dict:
    """Config view for the API: never expose secure-store refs (encrypted
    secret blobs) - only whether a credential is stored."""
    data = config.as_dict()
    router = data.get("router", {})
    for key, flag in (("password_ref", "has_password"), ("token_ref", "has_token")):
        router[flag] = bool(router.get(key))
        router[key] = ""
    return data


def create_runtime(home: Optional[Path] = None, demo: bool = False) -> Runtime:
    return Runtime(home=home, demo=demo)
