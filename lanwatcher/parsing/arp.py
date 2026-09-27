"""ARP table and neighbour table parsers (Windows `arp -a`, Linux `ip neigh`, `arp -a`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..util import normalize_mac


@dataclass
class NeighborEntry:
    ip: str
    mac: Optional[str]
    state: Optional[str] = None       # dynamic | static | Reachable | Stale | ...
    interface: Optional[str] = None   # interface name if derivable
    iface_ip: Optional[str] = None
    family: str = "ipv4"
    source: str = "arp"


_WIN_ARP_IFACE = re.compile(r"^Interface:\s*(\S+)\s*---\s*0x[0-9a-fA-F]+\s*$")
_WIN_ARP_ROW = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F:\-\.]{12,17})\s+(\w+)\s*$"
)
_LINUX_ARP_ROW = re.compile(
    r"^(?:\S+\s+)?\((\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+([0-9a-fA-F:\-]{11,17}|<incomplete>)"
    r"(?:\s+\[(\w+)\])?(?:\s+on\s+(\S+))?"
)
_IP_NEIGH_ROW = re.compile(
    r"^(\S+)\s+dev\s+(\S+)\s+(?:lladdr\s+(\S+)\s+)?([A-Z_]+)?\s*$"
)


def parse_arp_a(text: str) -> list:
    """Parse `arp -a` output. Handles both Windows and Linux/BSD styles."""
    entries: list = []
    iface_ip = None
    iface_name = None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _WIN_ARP_IFACE.match(line.strip())
        if m:
            iface_ip = m.group(1)
            iface_name = None
            continue
        m = _WIN_ARP_ROW.match(line)
        if m:
            mac = normalize_mac(m.group(2))
            entries.append(
                NeighborEntry(
                    ip=m.group(1),
                    mac=mac,
                    state=m.group(3).lower(),
                    iface_ip=iface_ip,
                    source="arp",
                )
            )
            continue
        m = _LINUX_ARP_ROW.match(line.strip())
        if m:
            mac = None if m.group(2) == "<incomplete>" else normalize_mac(m.group(2))
            entries.append(
                NeighborEntry(
                    ip=m.group(1),
                    mac=mac,
                    state=(m.group(3) or "").lower() or None,
                    interface=m.group(4),
                    source="arp",
                )
            )
    return _dedupe(entries)


def parse_ip_neigh(text: str, family: str = "ipv4") -> list:
    """Parse `ip neigh show` (Linux/Android) and `ip -6 neigh show`."""
    entries: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _IP_NEIGH_ROW.match(line)
        if not m:
            continue
        ip = m.group(1).split("%")[0]
        mac = normalize_mac(m.group(3)) if m.group(3) else None
        state = (m.group(4) or "").lower() or None
        if state == "failed":
            continue
        entries.append(
            NeighborEntry(ip=ip, mac=mac, state=state, interface=m.group(2), family=family, source="neighbor")
        )
    return _dedupe(entries)


def parse_netsh_neighbors(text: str, family: str = "ipv4") -> list:
    """Parse `netsh interface ipv4|ipv6 show neighbors` output.

    Produces entries with interface names (Windows 'Interface 12: Wi-Fi').
    """
    entries: list = []
    iface_name = None
    iface_idx = None
    iface_re = re.compile(r"^Interface\s+(\d+)\s*:\s*(.+?)\s*$")
    row_re = re.compile(
        r"^(\S+)\s+([0-9a-fA-F\-\.]{12,17}|<Link-layer address omitted>)\s+(\S.*?)\s*$"
    )
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = iface_re.match(line.strip())
        if m:
            iface_idx, iface_name = m.group(1), m.group(2).strip()
            continue
        if "Internet Address" in line or "Physical Address" in line or "-----" in line:
            continue
        m = row_re.match(line.strip())
        if not m:
            continue
        ip = m.group(1)
        if "/" in ip:  # IPv6 with prefix like fe80::1%12/64
            ip = ip.split("/")[0].split("%")[0]
        mac_raw = m.group(2)
        mac = None if mac_raw.startswith("<") else normalize_mac(mac_raw)
        state = m.group(3).strip()
        if state.lower() in ("unreachable", "incomplete"):
            mac = mac  # keep mac if present, but mark state
        entries.append(
            NeighborEntry(
                ip=ip.split("%")[0],
                mac=mac,
                state=state,
                interface=iface_name,
                iface_ip=iface_idx,
                family=family,
                source="neighbor",
            )
        )
    return _dedupe(entries)


def _dedupe(entries: list) -> list:
    """Keep the entry with a MAC when duplicates exist (last wins for state)."""
    out: dict = {}
    for e in entries:
        key = (e.ip, e.interface)
        prev = out.get(key)
        if prev is None or (prev.mac is None and e.mac is not None) or e.mac is not None:
            merged = e if prev is None or prev.mac == e.mac else e
            out[key] = merged
    return list(out.values())
