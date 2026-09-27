"""Router collector: gateway identity + optional authorized API + UPnP IGD.

Three tiers (all optional, graceful fallback):
1. Local route/gateway facts (always available via interface data)
2. UPnP IGD (router-published service, no credentials)
3. Operator-configured HTTP API (admin supplies legitimate credentials)
   Contract: GET {base_url}/lanwatcher/v1/snapshot with configured auth returns
   documented JSON (see docs/ROUTER-API.md). No credential guessing ever.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from typing import Optional

from ..models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    DeviceObservation,
    RouterSnapshot,
)
from ..util import is_valid_ipv4, normalize_mac
from .base import Collector, CollectorResult
from .upnp import UpnpRouterClient

log = logging.getLogger("lanwatcher.router")


class RouterCollector(Collector):
    name = "router"
    description = "Router/gateway via UPnP IGD + optional admin API"
    interval_s = 60.0

    def __init__(self, ctx, upnp: Optional[UpnpRouterClient] = None, http_opener=None):
        super().__init__(ctx)
        self._upnp = upnp or UpnpRouterClient()
        self._open = http_opener  # injectable for tests

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        snap = RouterSnapshot(source="router")
        found_any = False

        gw = self.ctx.state.get("gateway_ip")
        if gw:
            snap.set("gateway_ip", gw, "routes")
            snap.set("router_ip", gw, "routes")
            obs = DeviceObservation(source=self.name)
            obs.set("ipv4", gw, CONFIDENCE_HIGH)
            obs.set("category", "router", CONFIDENCE_HIGH)
            obs.set("online", True, CONFIDENCE_HIGH)
            result.observations.append(obs)
            found_any = True

        for iface in self.ctx.state.get("interfaces", []) or []:
            if getattr(iface, "ipv4_prefix", None) and getattr(iface, "ipv4", None):
                ip = iface.ipv4
                prefix = iface.ipv4_prefix
                parts = ip.split(".")
                net = ".".join(parts[:3] + ["0"]) if prefix == 24 else None
                if net:
                    snap.set("lan_subnet", f"{net}/{prefix}", "interfaces")
                if getattr(iface, "dns", None):
                    snap.set("dns_servers", iface.dns, "interfaces")
                if getattr(iface, "ipv6", None):
                    snap.set("ipv6_wan", None)  # never invent
                    snap.set("ipv6_lan", iface.ipv6, "interfaces")
                break

        # ---- UPnP IGD (authorized: router-published API)
        if self.ctx.config.get("router.upnp_enabled", True):
            try:
                info = self._upnp.query()
            except Exception as exc:  # noqa: BLE001
                log.debug("UPnP query failed: %s", exc)
                info = None
            if info:
                found_any = True
                snap.set("wan_status", info.get("wan_status"), "upnp")
                snap.set("wan_ip", info.get("wan_ip"), "upnp")
                snap.set("uptime_s", info.get("uptime_s"), "upnp")
                snap.set("model", info.get("model"), "upnp")
                snap.set("vendor", info.get("manufacturer"), "upnp")
                snap.set("hostname", info.get("friendly_name"), "upnp")
                snap.set("wan_upstream_bps", info.get("wan_upstream_bps"), "upnp")
                snap.set("wan_downstream_bps", info.get("wan_downstream_bps"), "upnp")

        # ---- operator-configured API
        if self.ctx.config.get("router.api_enabled", False):
            self._collect_api(snap, result)

        if not found_any and not snap.fields:
            result.unavailable_reason = "no router information source available (no gateway, UPnP off/absent, no API configured)"
            result.ok = False
            return result

        result.router = snap
        return result

    # ---------------------------------------------------------------- api

    def _collect_api(self, snap: RouterSnapshot, result: CollectorResult) -> None:
        base = (self.ctx.config.get("router.base_url") or "").rstrip("/")
        if not base:
            return
        url = base + "/lanwatcher/v1/snapshot"
        headers = {"Accept": "application/json", "User-Agent": "LAN-Watcher/1.0"}
        auth_type = self.ctx.config.get("router.auth_type", "none")
        secret = getattr(self.ctx, "secret_store", None)
        if auth_type == "basic":
            password = secret.unprotect(self.ctx.config.get("router.password_ref")) if secret else ""
            token = base64.b64encode(f"{self.ctx.config.get('router.username')}:{password or ''}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif auth_type == "bearer":
            token_val = secret.unprotect(self.ctx.config.get("router.token_ref")) if secret else ""
            if token_val:
                headers["Authorization"] = f"Bearer {token_val}"
        elif auth_type == "api_key":
            token_val = secret.unprotect(self.ctx.config.get("router.token_ref")) if secret else ""
            if token_val:
                headers[self.ctx.config.get("router.api_key_header", "X-API-Key")] = token_val

        req = urllib.request.Request(url, headers=headers, method="GET")
        timeout = float(self.ctx.config.get("router.timeout_s", 5.0))
        open_fn = self._open or urllib.request.urlopen
        try:
            with open_fn(req, timeout=timeout) as resp:
                payload = json.loads(resp.read(1024 * 1024).decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            log.debug("router API failed: %s", exc)
            snap.set("api_error", f"{type(exc).__name__}: {exc}", "api")
            return

        self.apply_router_snapshot(payload, snap, result)

    def apply_router_snapshot(self, payload: dict, snap: RouterSnapshot, result: CollectorResult) -> None:
        """Normalize the documented API JSON into RouterSnapshot + observations."""
        for key in (
            "router_ip", "gateway_ip", "hostname", "lan_subnet", "dhcp_range_start", "dhcp_range_end",
            "wan_ip", "wan_status", "model", "vendor", "ipv6_gateway", "ipv6_wan",
        ):
            if payload.get(key):
                snap.set(key, payload[key], "api")
        if payload.get("dns_servers"):
            snap.set("dns_servers", ",".join(payload["dns_servers"]) if isinstance(payload["dns_servers"], list) else payload["dns_servers"], "api")
        for key in ("uptime_s", "wan_uptime_s", "connected_clients", "lease_time_s"):
            if payload.get(key) is not None:
                snap.set(key, payload[key], "api")

        leases = payload.get("dhcp_leases") or payload.get("clients") or []
        normalized = []
        for lease in leases:
            if not isinstance(lease, dict):
                continue
            entry = {
                "ip": lease.get("ip") or lease.get("ipv4"),
                "ipv6": lease.get("ipv6"),
                "mac": normalize_mac(lease.get("mac")) or lease.get("mac"),
                "hostname": lease.get("hostname") or lease.get("name"),
                "lease_start": lease.get("lease_start"),
                "lease_end": lease.get("lease_end"),
                "interface": lease.get("interface"),
                "wireless": lease.get("wireless"),
                "vendor": lease.get("vendor"),
            }
            normalized.append(entry)
            obs = DeviceObservation(source="router_api")
            if entry["ip"] and is_valid_ipv4(entry["ip"]):
                obs.set("ipv4", entry["ip"], CONFIDENCE_HIGH)
            if entry["ipv6"]:
                obs.set("ipv6", entry["ipv6"], CONFIDENCE_HIGH)
            if entry["mac"]:
                obs.set("mac", entry["mac"], CONFIDENCE_HIGH)
            if entry["hostname"]:
                obs.set("hostname", entry["hostname"], CONFIDENCE_HIGH)
            if entry["vendor"]:
                obs.set("vendor", entry["vendor"], CONFIDENCE_MEDIUM)
            if entry["interface"]:
                obs.set("interface_name", entry["interface"], CONFIDENCE_HIGH)
            if entry["wireless"] is True:
                obs.set("connection_type", "wifi", CONFIDENCE_HIGH)
            elif entry["wireless"] is False:
                obs.set("connection_type", "ethernet", CONFIDENCE_HIGH)
            obs.set("online", True, CONFIDENCE_HIGH)
            obs.raw = {"role": "dhcp-lease"}
            if obs.fields:
                result.observations.append(obs)
        if normalized:
            snap.set("dhcp_leases", normalized, "api")
            snap.set("connected_clients", len(normalized), "api")
        result.router = snap
