"""Windows Neighbor Discovery (netsh) + Linux `ip neigh`. IPv4 and IPv6."""

from __future__ import annotations

from ..models import CONFIDENCE_HIGH, DeviceObservation
from ..parsing.arp import parse_ip_neigh, parse_netsh_neighbors
from ..util import is_link_local, is_loopback
from .base import Collector, CollectorResult

_MULTICAST_MACS = {"01005e000016", "01005e0000fb", "01005e7ffffa", "333300000001", "333300000053"}


class NeighborCollector(Collector):
    name = "neighbors"
    description = "Windows Neighbor Discovery / IPv6 neighbour table"
    interval_s = 30.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        entries = []
        if self.ctx.is_windows:
            for family in ("ipv4", "ipv6"):
                if family == "ipv6" and not self.ctx.config.get("general.ipv6_enabled", True):
                    continue
                res = self.ctx.runner.run(["netsh", "interface", family, "show", "neighbors"])
                if res.ok:
                    entries.extend(parse_netsh_neighbors(res.stdout, family=family))
        else:
            res = self.ctx.runner.run(["ip", "neigh", "show"])
            if res.ok:
                entries.extend(parse_ip_neigh(res.stdout))
            if self.ctx.config.get("general.ipv6_enabled", True):
                res6 = self.ctx.runner.run(["ip", "-6", "neigh", "show"])
                if res6.ok:
                    entries.extend(parse_ip_neigh(res6.stdout, family="ipv6"))

        if not entries:
            result.unavailable_reason = "neighbour table could not be read (API unavailable or restricted)"
            result.ok = False
            return result

        for e in entries:
            if e.mac in _MULTICAST_MACS or (e.mac or "").startswith(("01005e", "3333")):
                continue
            if e.ip and (is_loopback(e.ip) or is_link_local(e.ip)):
                continue
            obs = DeviceObservation(source=self.name)
            if e.family == "ipv6":
                obs.set("ipv6", e.ip, CONFIDENCE_HIGH)
            else:
                obs.set("ipv4", e.ip, CONFIDENCE_HIGH)
            if e.mac:
                obs.set("mac", e.mac, CONFIDENCE_HIGH)
            if e.interface:
                obs.set("interface_name", e.interface, "high")
            state = (e.state or "").lower()
            if state in ("stale", "delay", "probe", "reachable", "permanent", "connected"):
                obs.set("online", True, "medium")
            obs.raw = {"state": e.state}
            if obs.fields:
                result.observations.append(obs)
        return result
