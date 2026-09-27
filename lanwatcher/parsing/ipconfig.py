"""Parser for `ipconfig /all` (Windows). Extracts adapters, leases, DNS, gateways."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..util import normalize_mac

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"]
    )
}

_DATE_RE = re.compile(
    r"^(?P<dow>\w+),?\s+(?P<mon>\w+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})\s*(?P<ampm>AM|PM)?",
    re.IGNORECASE,
)


def parse_windows_datetime(value: str) -> Optional[str]:
    """'Thursday, September 24, 2026 8:15:03 PM' -> ISO-like 'YYYY-MM-DD HH:MM:SS' (local)."""
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group("mon").capitalize())
    if not mon:
        return None
    h = int(m.group("h"))
    ampm = (m.group("ampm") or "").upper()
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return f"{int(m.group('year')):04d}-{mon:02d}-{int(m.group('day')):02d} {h:02d}:{int(m.group('mi')):02d}:{int(m.group('s')):02d}"


@dataclass
class IpConfigAdapter:
    name: str
    description: Optional[str] = None
    mac: Optional[str] = None
    dhcp_enabled: Optional[bool] = None
    ipv4: Optional[str] = None
    subnet_mask: Optional[str] = None
    ipv4_extra: list = field(default_factory=list)
    ipv6: list = field(default_factory=list)
    gateways: list = field(default_factory=list)
    dns_servers: list = field(default_factory=list)
    dhcp_server: Optional[str] = None
    lease_obtained: Optional[str] = None
    lease_expires: Optional[str] = None
    connection_suffix: Optional[str] = None
    media_state: Optional[str] = None  # Media disconnected


@dataclass
class IpConfigResult:
    hostname: Optional[str] = None
    adapters: list = field(default_factory=list)


def parse_ipconfig_all(text: str) -> IpConfigResult:
    result = IpConfigResult()
    current: Optional[IpConfigAdapter] = None
    pending_list: Optional[str] = None  # continuation lines for DNS etc.

    adapter_re = re.compile(r"^(\S.*?)(?:\s+adapter)?\s+(.*?):\s*$")
    host_re = re.compile(r"Host Name[\s.]+:\s*(.+?)\s*$", re.IGNORECASE)

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        m = host_re.match(stripped)
        if m and current is None:
            result.hostname = m.group(1).strip()
            continue

        # Adapter header: line ending with ':' that has no ' : ' key/value split
        if stripped.endswith(":") and " . " not in stripped and not stripped.lower().startswith(("description",)):
            header = stripped[:-1].strip()
            # Keys like 'Physical Address. . . : value' never end with bare ':'
            if header and not re.search(r"\.\s*\.\s*\.", stripped):
                low = header.lower()
                if low.startswith(("ethernet", "wi-fi", "wireless", "local area", "bluetooth",
                                   "loopback", "vpn", "ppp", "wan miniport", "virtual", "hyper-v",
                                   "vethernet", "tap", "tunnel", "isatap", "teredo", "default ip")) \
                   or "adapter" in low:
                    # Short interface name: 'Wireless LAN adapter Wi-Fi' -> 'Wi-Fi'
                    short = re.sub(r"^.*?\badapter\s+", "", header, flags=re.IGNORECASE)
                    current = IpConfigAdapter(name=short or header)
                    current.name_full = header  # type: ignore[attr-defined]
                    result.adapters.append(current)
                    pending_list = None
                    continue

        if current is None:
            continue

        if " . " in stripped or re.search(r"\.\s*\.", stripped):
            key, value = _split_key_value(stripped)
        elif stripped and pending_list:
            # continuation of a multi-line list (DNS servers)
            _append_value(current, pending_list, stripped)
            continue
        else:
            continue

        if key is None:
            continue
        low = key.lower()
        pending_list = None

        if low.startswith("media state"):
            current.media_state = value or "Media disconnected"
        elif low.startswith("connection-specific dns suffix"):
            current.connection_suffix = value or None
        elif low.startswith("description"):
            current.description = value or None
        elif low.startswith("physical address"):
            current.mac = normalize_mac(value)
        elif low.startswith("dhcp enabled"):
            current.dhcp_enabled = value.strip().lower().startswith("yes")
        elif low.startswith("ipv4 address") or (low.startswith("ip address") and ":" not in value):
            ip = _clean_ip(value)
            if ip:
                if current.ipv4 is None:
                    current.ipv4 = ip
                else:
                    current.ipv4_extra.append(ip)
        elif low.startswith("ipv6 address") or low.startswith("link-local ipv6"):
            ip = _clean_ip(value)
            if ip:
                current.ipv6.append(ip)
        elif low.startswith("subnet mask"):
            current.subnet_mask = _clean_ip(value)
        elif low.startswith("default gateway"):
            gw = _clean_ip(value)
            if gw:
                current.gateways.append(gw)
            pending_list = "gateways"
        elif low.startswith("dhcp server"):
            current.dhcp_server = _clean_ip(value)
        elif low.startswith("dns servers"):
            v = _clean_ip(value)
            if v:
                current.dns_servers.append(v)
            pending_list = "dns_servers"
        elif low.startswith("lease obtained"):
            current.lease_obtained = parse_windows_datetime(value) if value else None
        elif low.startswith("lease expires"):
            current.lease_expires = parse_windows_datetime(value) if value else None

    return result


def _split_key_value(stripped: str):
    m = re.match(r"^(.+?)[\s.]{2,}:\s*(.*)$", stripped)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if ":" in stripped:
        k, v = stripped.split(":", 1)
        return k.strip().rstrip(".").strip(), v.strip()
    return None, None


def _clean_ip(value: str) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    v = re.sub(r"\(.*?\)", "", v).strip()          # remove (Preferred)
    v = v.split("%")[0].split("/")[0].strip()
    return v or None


def _append_value(current: IpConfigAdapter, which: str, value: str) -> None:
    v = _clean_ip(value)
    if not v:
        return
    getattr(current, which).append(v)
