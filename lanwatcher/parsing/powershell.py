"""Parsers for PowerShell JSON output (Get-NetAdapter, Get-NetIPConfiguration).

PowerShell runs only at slow cadence for adapter/link details; all parsing is
pure so it can be tested with captured JSON without Windows.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..models import InterfaceInfo
from ..util import normalize_mac

_SPEED_RE = re.compile(r"([\d.,]+)\s*(bps|kbps|mbps|gbps|tbps)", re.IGNORECASE)

_VIRTUAL_HINTS = ("hyper-v", "vethernet", "vmware", "virtualbox", "tap-windows", "wintun", "vpn", "tun ", "tap ", "virtual")
_VIRTUAL_NAME_HINTS = ("vethernet", "hyper-v", "virtual", "vmware", "virtualbox", "tap-windows", "wintun", "vpn", "vnic")
_WIFI_HINTS = ("wi-fi", "wifi", "wireless", "802.11", "wlan")
_ETHERNET_HINTS = ("ethernet", "gbe", "fast ethernet", "gigabit", "2.5gbe", "realtek gaming", "thunderbolt ethernet")
_OTHER_HINTS = ("loopback", "bluetooth")


def parse_link_speed(value: Optional[str]) -> Optional[int]:
    """'1 Gbps' -> 1_000_000_000. '100 Mbps' -> 100_000_000."""
    if not value:
        return None
    m = _SPEED_RE.search(value.replace(",", ""))
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).lower()
    mult = {"bps": 1, "kbps": 1000, "mbps": 1000**2, "gbps": 1000**3, "tbps": 1000**4}[unit]
    return int(num * mult)


def classify_interface(description: Optional[str], name: Optional[str]) -> str:
    """Best-effort medium classification. Virtual adapters win over name hints
    (e.g. 'TAP-Windows Adapter V9' on 'Ethernet 2' is virtual, not wired)."""
    desc = (description or "").lower()
    nm = (name or "").lower()
    hay = f"{nm} {desc}"
    if any(h in desc for h in _VIRTUAL_HINTS) or any(h in nm for h in _VIRTUAL_NAME_HINTS):
        return "other"
    if any(h in desc for h in _OTHER_HINTS) or any(h in nm for h in ("loopback", "bluetooth")):
        return "other"
    if any(h in desc for h in _WIFI_HINTS) or any(h in nm for h in _WIFI_HINTS):
        return "wifi"
    if any(h in desc for h in _ETHERNET_HINTS) or any(h in nm for h in _ETHERNET_HINTS):
        return "ethernet"
    if any(h in hay for h in _WIFI_HINTS):
        return "wifi"
    if any(h in hay for h in _ETHERNET_HINTS):
        return "ethernet"
    return "unknown"


def _ps_json(text: str):
    text = text.strip()
    if not text:
        return None
    # PowerShell ConvertTo-Json may emit BOM or warnings before JSON
    idx = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if idx < 0:
        return None
    try:
        return json.loads(text[idx:])
    except ValueError:
        return None


def parse_net_adapter_json(text: str) -> list:
    """Get-NetAdapter | Select-Object ... | ConvertTo-Json -> InterfaceInfo list."""
    data = _ps_json(text)
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    out: list = []
    for item in data:
        name = item.get("Name") or item.get("InterfaceAlias") or item.get("InterfaceDescription")
        desc = item.get("InterfaceDescription") or item.get("Description")
        status = (item.get("Status") or "").strip().lower()
        out.append(
            InterfaceInfo(
                name=name or "?",
                description=desc,
                mac=normalize_mac(item.get("MacAddress")),
                kind=classify_interface(desc, name),
                is_up=1 if status in ("up", "connected") else 0,
                link_speed_bps=parse_link_speed(item.get("LinkSpeed")),
                mtu=int(item["NlMtu"]) if isinstance(item.get("NlMtu"), (int, float)) else None,
                source="netadapter",
            )
        )
    return out


def parse_net_ip_configuration_json(text: str) -> dict:
    """Get-NetIPConfiguration | ConvertTo-Json -> {interface_name: ip details}.

    Returns mapping of InterfaceAlias -> {ipv4, ipv4_prefix, ipv6, gateway, dns}.
    """
    data = _ps_json(text)
    if data is None:
        return {}
    if isinstance(data, dict):
        data = [data]
    out: dict = {}
    for item in data:
        alias = item.get("InterfaceAlias") or (item.get("Interface") or {}).get("InterfaceAlias")
        if not alias:
            continue
        entry = {"ipv4": None, "ipv4_prefix": None, "ipv6": None, "gateway": None, "dns": []}
        v4s = item.get("IPv4Address") or []
        if isinstance(v4s, dict):
            v4s = [v4s]
        for a in v4s:
            if a.get("IPAddress"):
                entry["ipv4"] = a["IPAddress"]
                entry["ipv4_prefix"] = a.get("PrefixLength")
                break
        v6s = item.get("IPv6Address") or []
        if isinstance(v6s, dict):
            v6s = [v6s]
        for a in v6s:
            ip = a.get("IPAddress") or ""
            if ip and not ip.lower().startswith("fe80"):
                entry["ipv6"] = ip
                break
        gws = item.get("IPv4DefaultGateway") or item.get("IPv6DefaultGateway") or []
        if isinstance(gws, dict):
            gws = [gws]
        for g in gws:
            if g.get("NextHop"):
                entry["gateway"] = g["NextHop"]
                break
        dnss = (item.get("DNSServer") or [])
        if isinstance(dnss, dict):
            dnss = [dnss]
        for d in dnss:
            if d.get("Address"):
                entry["dns"].append(d["Address"])
        out[alias] = entry
    return out
