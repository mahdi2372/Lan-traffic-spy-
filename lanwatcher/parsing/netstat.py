"""`netstat -ano` flow metadata parser. Endpoint metadata only - never payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_TCP_RE = re.compile(
    r"^\s*(TCP|UDP)\s+(\S+):(\d+|\*)\s+(\S+?)(?::(\d+|\*))?\s*(?:(\w+)\s+)?(\d+)?\s*$"
)


@dataclass
class NetstatRow:
    protocol: str
    local_ip: str
    local_port: Optional[int]
    remote_ip: Optional[str]
    remote_port: Optional[int]
    state: Optional[str]
    pid: Optional[int]


def _split_endpoint(ep: str):
    """'192.168.1.5:443' / '[fe80::1%12]:80' / '0.0.0.0:*' / '*:*'"""
    ep = ep.strip()
    if ep.startswith("["):
        host, _, rest = ep[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = ep.rpartition(":")
    port_i: Optional[int]
    try:
        port_i = int(port) if port and port != "*" else None
    except ValueError:
        port_i = None
    host = host.strip("[]")
    if host == "*":
        host = None
    return host, port_i


def parse_netstat_ano(text: str) -> list:
    rows: list = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.lower()
        if low.startswith("active") or low.strip().startswith("proto") or "local address" in low:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() not in ("TCP", "UDP"):
            continue
        proto = parts[0].upper()
        local_ip, local_port = _split_endpoint(parts[1])
        remote_ip = remote_port = state = None
        pid = None
        if len(parts) >= 3:
            maybe_state = parts[2].upper()
            if proto == "UDP" and not re.match(r"^\[?[\w\.\:\%\*]+\]?:", parts[2]):
                # UDP: second token can be '*:*' then PID
                if parts[2] in ("*:*",):
                    pid = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
                    rows.append(NetstatRow(proto, local_ip or "", local_port, None, None, None, pid))
                    continue
            remote_ip, remote_port = _split_endpoint(parts[2])
        if len(parts) >= 4 and parts[3].isalpha():
            state = parts[3]
            if len(parts) >= 5 and parts[4].isdigit():
                pid = int(parts[4])
        elif len(parts) >= 4 and parts[3].isdigit():
            pid = int(parts[3])
        rows.append(NetstatRow(proto, local_ip or "", local_port, remote_ip, remote_port, state, pid))
    return rows
