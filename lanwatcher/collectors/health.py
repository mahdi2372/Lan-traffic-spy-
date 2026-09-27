"""Network health collector: observable metrics with explanations, no invented diagnoses."""

from __future__ import annotations

import socket
import time
import urllib.request

from ..models import HealthComponent, HealthSnapshot
from ..services.ping import Pinger
from ..util import parse_iso, utc_now
from .base import Collector, CollectorResult


class HealthCollector(Collector):
    name = "health"
    description = "Gateway latency/loss, DNS, internet, DHCP, local availability"
    interval_s = 30.0

    def __init__(self, ctx, pinger: Pinger = None, http_opener=None):
        super().__init__(ctx)
        self.pinger = pinger or Pinger()
        self._open = http_opener

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        cfg = self.ctx.config
        components: list = []

        gw = self.ctx.state.get("gateway_ip")
        if gw:
            avg, loss = self.pinger.ping_stats(gw, count=3, timeout_s=1.0)
            if avg is None:
                components.append(
                    HealthComponent(
                        name="gateway_latency",
                        status="down",
                        detail="no reply",
                        explanation="The gateway did not answer ICMP echo requests. This can mean the router is down, busy, or configured to ignore ping.",
                        latency_ms=None,
                        loss_pct=loss,
                    )
                )
            elif loss > 0 or avg > 50:
                components.append(
                    HealthComponent(
                        name="gateway_latency",
                        status="degraded",
                        detail=f"{avg:.1f} ms, {loss:.0f}% loss",
                        explanation="The gateway replied slowly or dropped probes. This can indicate Wi-Fi interference, a loaded router, or a busy uplink.",
                        latency_ms=avg,
                        loss_pct=loss,
                    )
                )
            else:
                components.append(
                    HealthComponent(
                        name="gateway_latency",
                        status="ok",
                        detail=f"{avg:.1f} ms, {loss:.0f}% loss",
                        latency_ms=avg,
                        loss_pct=loss,
                    )
                )
        else:
            components.append(
                HealthComponent(
                    name="gateway_latency",
                    status="unknown",
                    detail="no gateway detected",
                    explanation="No default gateway was found in the routing table, so gateway latency cannot be measured.",
                    measured=False,
                )
            )

        # ---- DNS response time
        dns_name = cfg.get("health.dns_probe_name", "dns.msftncsi.com")
        local_only = cfg.get("privacy.local_only_mode", False)
        if local_only:
            components.append(
                HealthComponent(
                    name="dns",
                    status="unavailable",
                    detail="skipped",
                    explanation="Local-only mode is enabled: external DNS probes are disabled by design.",
                    measured=False,
                )
            )
        else:
            t0 = time.perf_counter()
            try:
                socket.setdefaulttimeout(3.0)
                socket.getaddrinfo(dns_name, 80)
                dns_ms = (time.perf_counter() - t0) * 1000.0
                status = "ok" if dns_ms < 200 else "degraded"
                components.append(
                    HealthComponent(
                        name="dns",
                        status=status,
                        detail=f"{dns_ms:.0f} ms to resolve {dns_name}",
                        explanation="" if status == "ok" else "DNS resolution took longer than 200 ms. This can indicate a slow or overloaded resolver.",
                        latency_ms=dns_ms,
                    )
                )
            except OSError as exc:
                components.append(
                    HealthComponent(
                        name="dns",
                        status="down",
                        detail=str(exc),
                        explanation="Name resolution failed. This can indicate DNS server problems or that the uplink is offline.",
                    )
                )

        # ---- internet reachability (visible, documented, toggleable)
        if cfg.get("privacy.internet_probe_enabled", False) and not local_only:
            url = cfg.get("privacy.internet_probe_url")
            t0 = time.perf_counter()
            try:
                open_fn = self._open or urllib.request.urlopen
                with open_fn(url, timeout=3.0) as resp:
                    ok = 200 <= getattr(resp, "status", 200) < 400
                ms = (time.perf_counter() - t0) * 1000.0
                components.append(
                    HealthComponent(
                        name="internet",
                        status="ok" if ok else "degraded",
                        detail=f"probe {url} ({ms:.0f} ms)",
                        latency_ms=ms,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                components.append(
                    HealthComponent(
                        name="internet",
                        status="down",
                        detail=f"probe {url} failed",
                        explanation=f"The HTTP reachability probe failed ({type(exc).__name__}). This can mean the internet connection is down, or a captive portal/firewall blocks the probe.",
                    )
                )
        else:
            components.append(
                HealthComponent(
                    name="internet",
                    status="unavailable",
                    detail="probe disabled",
                    explanation="Internet reachability probing is disabled in Privacy settings (or local-only mode is on), so this metric is not measured.",
                    measured=False,
                )
            )

        # ---- local network availability (any neighbour answering recently?)
        recent_online = 0
        components.append(
            HealthComponent(
                name="local_network",
                status="ok" if self.ctx.state.get("has_recent_sightings", True) else "degraded",
                detail="based on device discovery results",
                explanation=""
                if self.ctx.state.get("has_recent_sightings", True)
                else "No LAN devices answered discovery probes recently. This can indicate an isolated interface or a quiet network.",
            )
        )

        # ---- DHCP availability
        lease_exp = self.ctx.state.get("lease_expires")
        dhcp_server = self.ctx.state.get("dhcp_server")
        if not lease_exp and not dhcp_server:
            components.append(
                HealthComponent(
                    name="dhcp",
                    status="unavailable",
                    detail="no DHCP lease information",
                    explanation="The host uses static configuration or the OS exposes no lease data, so DHCP availability cannot be measured.",
                    measured=False,
                )
            )
        else:
            exp = parse_iso(str(lease_exp).replace(" ", "T") + "Z") if lease_exp and len(str(lease_exp)) == 19 else None
            if exp is not None:
                remaining = (exp - utc_now()).total_seconds()
                if remaining <= 0:
                    components.append(
                        HealthComponent(
                            name="dhcp",
                            status="degraded",
                            detail="lease expired",
                            explanation="The DHCP lease expiry time is in the past. Renewal may be failing; connectivity can drop when the lease is not renewed.",
                        )
                    )
                else:
                    components.append(
                        HealthComponent(
                            name="dhcp",
                            status="ok",
                            detail=f"lease valid for {remaining / 3600:.1f} h",
                        )
                    )
            else:
                components.append(
                    HealthComponent(
                        name="dhcp",
                        status="unknown",
                        detail=f"server {dhcp_server}" if dhcp_server else "lease data present",
                        explanation="Lease timestamps are present but not parseable, so lease health cannot be evaluated.",
                    )
                )

        # ---- device availability (share of known devices currently online)
        known = self.ctx.state.get("known_device_counts") or {}
        total = known.get("total", 0)
        online = known.get("online", 0)
        if total:
            pct = 100.0 * online / total
            components.append(
                HealthComponent(
                    name="devices",
                    status="ok" if pct >= 30 else "degraded",
                    detail=f"{online}/{total} devices online",
                    explanation=""
                    if pct >= 30
                    else "Fewer than 30% of known devices are reachable. This can be normal outside working hours, or indicate a network problem.",
                )
            )
        else:
            components.append(
                HealthComponent(name="devices", status="unknown", detail="no known devices yet", measured=False)
            )

        snap = self._score(components)
        result.health = snap
        return result

    def _score(self, components: list) -> HealthSnapshot:
        weights = {"ok": 1.0, "degraded": 0.5, "down": 0.0, "unknown": None, "unavailable": None}
        scores = []
        worst = "ok"
        order = {"ok": 0, "unavailable": 0, "unknown": 0, "degraded": 1, "down": 2}
        for c in components:
            w = weights.get(c.status)
            if w is not None:
                scores.append(w)
            if order.get(c.status, 0) > order.get(worst, 0):
                worst = c.status
        score = int(round(100 * sum(scores) / len(scores))) if scores else 0
        overall = {"ok": "healthy", "degraded": "degraded", "down": "critical"}.get(worst, "unknown")
        return HealthSnapshot(overall=overall, score=score, components=components)
