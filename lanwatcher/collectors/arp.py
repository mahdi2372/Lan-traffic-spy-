"""ARP table collector: Windows `arp -a`, Linux `ip neigh`/`arp -a`."""

from __future__ import annotations

from ..models import CONFIDENCE_HIGH, DeviceObservation
from ..parsing.arp import parse_arp_a, parse_ip_neigh
from ..util import is_broadcast_mac, is_link_local, is_loopback, is_multicast
from .base import Collector, CollectorResult


class ARPCollector(Collector):
    name = "arp"
    description = "ARP table (Layer-2 adjacency cache)"
    interval_s = 30.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        text = None
        if self.ctx.is_windows or True:
            res = self.ctx.runner.run(["arp", "-a"])
            if res.ok:
                text = res.stdout
        entries = []
        if text:
            entries = parse_arp_a(text)
        if not entries and not self.ctx.is_windows:
            res = self.ctx.runner.run(["ip", "neigh", "show"])
            if res.ok:
                entries = parse_ip_neigh(res.stdout)
        if not entries and text is None and not self.ctx.is_windows:
            res = self.ctx.runner.run(["ip", "-6", "neigh", "show"])
            if res.ok:
                entries.extend(parse_ip_neigh(res.stdout, family="ipv6"))
        if not entries:
            result.unavailable_reason = "ARP table could not be read (command missing or restricted)"
            result.ok = False
            return result

        for e in entries:
            if e.ip and (is_loopback(e.ip) or is_link_local(e.ip) or is_multicast(e.ip)):
                continue
            if e.mac is None and e.state in ("incomplete", "failed"):
                continue
            if e.mac is None and e.state == "static":
                continue
            if is_broadcast_mac(e.mac) or (e.mac or "").startswith("01005e"):
                continue
            obs = DeviceObservation(source=self.name)
            if e.family == "ipv6":
                obs.set("ipv6", e.ip, CONFIDENCE_HIGH)
            else:
                obs.set("ipv4", e.ip, CONFIDENCE_HIGH)
            if e.mac:
                obs.set("mac", e.mac, CONFIDENCE_HIGH)
            if e.interface:
                obs.set("interface_name", e.interface, "medium")
            obs.set("online", True, "medium")
            obs.raw = {"state": e.state, "iface_ip": e.iface_ip}
            if obs.fields:
                result.observations.append(obs)
        return result
