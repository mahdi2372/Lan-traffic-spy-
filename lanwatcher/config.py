"""Configuration system: JSON file per user, validated defaults, change events.

No cloud services, no telemetry, no hidden network calls. Anything that touches
the network is a named, documented, toggleable feature (see privacy section).
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .util import app_dir

log = logging.getLogger("lanwatcher.config")

DEFAULTS: dict = {
    "general": {
        "scan_interval_s": 30,          # discovery cycle period
        "offline_threshold_s": 120,     # no sighting for this long => offline
        "ping_enabled": True,           # ICMP reachability probes (rate-limited)
        "ping_concurrency": 16,
        "subnet_scan_enabled": True,    # probe on-link addresses from interface subnets
        "ipv6_enabled": True,
    },
    "privacy": {
        "local_only_mode": False,       # disables ALL external network probes
        "internet_probe_enabled": True,  # HTTP reachability probe (documented)
        "internet_probe_url": "http://www.msftconnecttest.com/connecttest.txt",
        "encrypt_notes": False,         # encrypt user notes + router credentials at rest
        "telemetry_enabled": False,     # locked off; never enable
    },
    "retention": {
        "flows_days": 7,
        "events_days": 90,
        "sessions_days": 180,
        "alerts_days": 180,
        "vacuum_on_purge": True,
    },
    "alerts": {
        "new_device": True,
        "device_offline": False,
        "device_online": False,
        "ip_change": True,
    },
    "router": {
        "enabled": True,
        "upnp_enabled": True,
        "api_enabled": False,
        "base_url": "",
        "auth_type": "none",  # none | basic | bearer | api_key
        "username": "",
        "password_ref": "",   # secure-store token
        "token_ref": "",
        "api_key_header": "X-API-Key",
        "verify_tls": True,
        "timeout_s": 5.0,
    },
    "traffic": {
        "enabled": True,
        "interval_s": 5,
        "per_flow_bytes": False,   # requires elevation on Windows; off by default
    },
    "health": {
        "interval_s": 30,
        "dns_probe_name": "dns.msftncsi.com",
    },
    "discovery": {
        "arp": True,
        "neighbors": True,
        "router_clients": True,
        "resolve_hostnames": True,
    },
    "ui": {
        "theme": "dark",
        "language": "en",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else app_dir() / "config.json"
        self._lock = threading.RLock()
        self._data: dict = copy.deepcopy(DEFAULTS)
        self._listeners: list = []
        self.load()

    # -------------------------------------------------- persistence

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._data = _deep_merge(DEFAULTS, raw)
                except (OSError, ValueError):
                    log.exception("config unreadable, using defaults")
                    self._data = copy.deepcopy(DEFAULTS)
            self._sanitize()

    def save(self) -> None:
        with self._lock:
            self._sanitize()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def _sanitize(self) -> None:
        g = self._data["general"]
        g["scan_interval_s"] = max(5, min(3600, int(g.get("scan_interval_s", 30))))
        g["offline_threshold_s"] = max(15, min(86400, int(g.get("offline_threshold_s", 120))))
        g["ping_concurrency"] = max(1, min(128, int(g.get("ping_concurrency", 16))))
        r = self._data["retention"]
        for key in ("flows_days", "events_days", "sessions_days", "alerts_days"):
            r[key] = max(1, min(3650, int(r.get(key, 30))))
        self._data["privacy"]["telemetry_enabled"] = False  # locked off
        if self._data["privacy"].get("local_only_mode"):
            self._data["privacy"]["internet_probe_enabled"] = False
        t = self._data["traffic"]
        t["interval_s"] = max(1, min(300, int(t.get("interval_s", 5))))

    # -------------------------------------------------- access

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, dotted: str, value: Any, persist: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            self._sanitize()
            if persist:
                self.save()
        self._notify(dotted, value)

    def update(self, values: dict, persist: bool = True) -> None:
        """values: {'general.scan_interval_s': 15, ...}"""
        with self._lock:
            for dotted, value in values.items():
                parts = dotted.split(".")
                node = self._data
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            self._sanitize()
            if persist:
                self.save()
        for dotted in values:
            self._notify(dotted, self.get(dotted))

    def as_dict(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def reset(self) -> None:
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            self.save()
        self._notify("*", None)

    # -------------------------------------------------- change notifications

    def on_change(self, fn: Callable[[str, Any], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _notify(self, key: str, value: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(key, value)
            except Exception:  # noqa: BLE001
                log.exception("config listener failed")
