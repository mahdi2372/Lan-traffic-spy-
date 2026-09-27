"""DHCP collector: local lease facts + DHCP server reachability metadata.

Full client/lease tables come from the optional authorized router API
(see RouterCollector). This collector never attempts DHCP server logins.
"""

from __future__ import annotations

from ..models import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, DeviceObservation, RouterSnapshot
from ..util import iso, parse_iso, utc_now
from .base import Collector, CollectorResult


class DHCPCollector(Collector):
    name = "dhcp"
    description = "DHCP lease facts (local client + server reachability)"
    interval_s = 60.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        lease_obtained = self.ctx.state.get("lease_obtained")
        lease_expires = self.ctx.state.get("lease_expires")
        dhcp_server = self.ctx.state.get("dhcp_server")
        if not lease_obtained and not dhcp_server:
            result.unavailable_reason = "DHCP information unavailable (static config or restricted)"
            result.ok = False
            return result

        snap = RouterSnapshot(source=self.name)
        snap.set("dhcp_server", dhcp_server)
        if lease_expires:
            exp = parse_iso(lease_expires.replace(" ", "T") + "Z") if len(lease_expires) == 19 else None
            if exp:
                snap.set("lease_time_s", max(0.0, (exp - utc_now()).total_seconds()))
        result.router = snap

        if dhcp_server:
            obs = DeviceObservation(source=self.name)
            obs.set("ipv4", dhcp_server, CONFIDENCE_HIGH)
            obs.set("category", "router", CONFIDENCE_MEDIUM)
            result.observations.append(obs)
        return result
