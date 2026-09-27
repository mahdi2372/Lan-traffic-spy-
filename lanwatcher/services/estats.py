"""Windows IP Helper TCP EStats (per-connection byte counters).

ENTIRELY OPTIONAL: requires the operator to enable 'per-flow byte counters' in
Settings AND run elevated (Windows gates the IP Helper statistics APIs behind
elevation). When unavailable, flow records simply keep bytes=None and the UI
shows '—'. Read-only: never modifies connections or traffic.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Optional

log = logging.getLogger("lanwatcher.estats")

TCP_CONNECTION_ESTATS_DATA = 2


class _MibTcpRowOwnerModule(ctypes.Structure):
    _fields_ = [
        ("dwState", ctypes.c_uint32),
        ("dwLocalAddr", ctypes.c_uint32),
        ("dwLocalPort", ctypes.c_uint32),
        ("dwRemoteAddr", ctypes.c_uint32),
        ("dwRemotePort", ctypes.c_uint32),
        ("dwOwningPid", ctypes.c_uint32),
        ("liCreateTimestamp", ctypes.c_int64),
        ("liCreateTimestamp2", ctypes.c_int64),  # alignment padding union
        ("OwningModuleInfo", ctypes.c_uint64 * 16),
    ]


class _TcpEstatsDataRod(ctypes.Structure):
    _fields_ = [
        ("DataBytesOut", ctypes.c_uint64),
        ("DataSegsOut", ctypes.c_uint64),
        ("DataBytesIn", ctypes.c_uint64),
        ("DataSegsIn", ctypes.c_uint64),
        ("Elapsed", ctypes.c_uint64),
        ("TimeStampResolution", ctypes.c_uint32),
        ("TimeStampCapacity", ctypes.c_uint32),
    ]


_STATE_MAP = {
    "closed": 1, "listen": 2, "syn_sent": 3, "syn_rcvd": 4,
    "established": 5, "fin_wait1": 6, "fin_wait2": 7, "close_wait": 8,
    "closing": 9, "last_ack": 10, "time_wait": 11, "delete_tcb": 12,
}


def _ip_to_dword(ip: str) -> int:
    import socket

    packed = socket.inet_pton(socket.AF_INET, ip)
    # MIB stores IPv4 in network byte order in a DWORD
    return int.from_bytes(packed, "little")


def _port_to_word(port: int) -> int:
    # dwRemotePort/dwLocalPort: port in network byte order in the low 16 bits
    return ((port & 0xFF) << 8) | ((port >> 8) & 0xFF)


def enrich_flow_bytes(flows: list) -> int:
    """Attach bytes=local_tx+local_rx estimates for local-side TCP flows.

    Returns the number of flows enriched. Any failure degrades to no-op.
    """
    try:
        import psutil
    except ImportError:
        return 0
    import sys

    if not sys.platform.startswith("win"):
        return 0
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:  # noqa: BLE001
        return 0

    estats = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    enriched = 0
    for c in conns:
        if not c.laddr or not c.raddr:
            continue
        try:
            row = _MibTcpRowOwnerModule()
            row.dwState = _STATE_MAP.get((c.status or "").lower(), 5)
            row.dwLocalAddr = _ip_to_dword(c.laddr.ip)
            row.dwLocalPort = _port_to_word(c.laddr.port)
            row.dwRemoteAddr = _ip_to_dword(c.raddr.ip)
            row.dwRemotePort = _port_to_word(c.raddr.port)
            row.dwOwningPid = c.pid or 0
            rod = _TcpEstatsDataRod()
            err = estats.GetPerTcpConnectionEStats(
                ctypes.byref(row),
                TCP_CONNECTION_ESTATS_DATA,
                None,
                0,
                None,
                0,
                ctypes.byref(rod),
            )
            if err != 0:
                continue
            total = int(rod.DataBytesOut + rod.DataBytesIn)
            for f in flows:
                if (
                    f.src_ip == c.laddr.ip
                    and f.src_port == c.laddr.port
                    and f.dst_ip == c.raddr.ip
                    and f.dst_port == c.raddr.port
                ):
                    f.bytes = total
                    f.duration_s = float(rod.Elapsed) / 1e7 if rod.Elapsed else None
                    enriched += 1
                    break
        except Exception:  # noqa: BLE001
            continue
    return enriched
