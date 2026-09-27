"""`route print` parser: default gateway, on-link routes, interface list."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RouteRow:
    destination: str
    netmask: str
    gateway: str
    interface: str
    metric: Optional[int] = None


@dataclass
class RouteTable:
    default_gateway: Optional[str] = None
    default_interface: Optional[str] = None
    routes: list = field(default_factory=list)
    interfaces: list = field(default_factory=list)


_IPV4_ROUTE_RE = re.compile(
    r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(\d{1,3}(?:\.\d{1,3}){3}|On-link)\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s*$"
)


def parse_route_print(text: str) -> RouteTable:
    table = RouteTable()
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if "ipv4 route table" in low:
            section = "ipv4"
            continue
        if "ipv6 route table" in low:
            section = "ipv6"
            continue
        if "interface list" in low:
            section = "ifaces"
            continue
        if set(line) <= {"=", "-", " "}:
            continue
        if section == "ipv4":
            m = _IPV4_ROUTE_RE.match(line)
            if not m:
                continue
            row = RouteRow(
                destination=m.group(1),
                netmask=m.group(2),
                gateway=m.group(3),
                interface=m.group(4),
                metric=int(m.group(5)),
            )
            table.routes.append(row)
            if row.destination == "0.0.0.0" and row.netmask == "0.0.0.0":
                if table.default_gateway is None or (row.metric or 9999) < (
                    next((r.metric or 9999 for r in table.routes[:-1] if r.destination == "0.0.0.0"), 9999)
                ):
                    table.default_gateway = row.gateway if row.gateway != "On-link" else None
                    table.default_interface = row.interface
        elif section == "ifaces":
            # ' 12...aa bb cc dd ee ff ......Intel(R) Wi-Fi 6 AX201'
            m = re.match(r"^(\d+)\.\.\.((?:[0-9a-f]{2} ){5}[0-9a-f]{2}|\.{3,})\.*(.*)$", line, re.IGNORECASE)
            if m:
                table.interfaces.append(
                    {
                        "idx": int(m.group(1)),
                        "mac": m.group(2).strip().replace(" ", "") if " " in m.group(2).strip() else None,
                        "description": m.group(3).strip() or None,
                    }
                )
    return table
