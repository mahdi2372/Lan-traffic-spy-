"""Parsers for `netsh` outputs: wlan interfaces/networks, interface admin state,
IP interface metrics, firewall profile. English label formats are supported;
unknown labels degrade gracefully to ignored fields (never crash)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import WifiLinkInfo
from ..util import normalize_mac

_KV_RE = re.compile(r"^\s*(.+?)\s{2,}:\s*(.*?)\s*$")


def _kv(line: str):
    m = _KV_RE.match(line)
    if m:
        return m.group(1).strip().lower(), m.group(2).strip()
    if ":" in line:
        k, v = line.split(":", 1)
        return k.strip().lower(), v.strip()
    return None, None


def _to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    m = re.search(r"-?\d+", value.replace(",", ""))
    return int(m.group(0)) if m else None


def _to_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(m.group(0)) if m else None


def _band(freq_mhz: Optional[int]) -> Optional[str]:
    if not freq_mhz:
        return None
    if 2400 <= freq_mhz <= 2500:
        return "2.4 GHz"
    if 4900 <= freq_mhz <= 5900:
        return "5 GHz"
    if 5925 <= freq_mhz <= 7125:
        return "6 GHz"
    return None


def channel_to_freq(channel: Optional[int]) -> Optional[int]:
    if not channel:
        return None
    if 1 <= channel <= 14:
        return 2407 + channel * 5 if channel != 14 else 2484
    if 32 <= channel <= 177:
        return 5000 + channel * 5
    return None


# ---------------------------------------------------------------- wlan


def parse_wlan_interfaces(text: str) -> list:
    """`netsh wlan show interfaces` -> WifiLinkInfo list.

    Never extracts or attempts to extract Wi-Fi passwords.
    """
    links: list = []
    current: Optional[dict] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        key, value = _kv(line)
        if key is None:
            continue
        if key in ("name",) and not current.get("interface") if current else key == "name":
            pass
        if key == "name":
            current = {"interface": value}
            links.append(current)
            continue
        if current is None:
            continue
        if key == "description":
            current["description"] = value
        elif key == "physical address":
            current["bssid_or_mac_hint"] = value
            current["mac"] = normalize_mac(value)
        elif key == "state":
            current["state"] = value.lower()
        elif key == "ssid":
            current["ssid"] = value
        elif key == "bssid":
            current["bssid"] = normalize_mac(value)
        elif key == "network type":
            current["network_type"] = value
        elif key == "radio type":
            current["radio_type"] = value
        elif key in ("authentication", "authentication and cipher") :
            current["security"] = value
        elif key == "channel":
            current["channel"] = _to_int(value)
        elif key.startswith("receive rate"):
            current["receive_rate_mbps"] = _to_float(value)
        elif key.startswith("transmit rate"):
            current["transmit_rate_mbps"] = _to_float(value)
        elif key == "signal":
            current["signal_pct"] = _to_int(value)
        elif key == "profile":
            current["profile"] = value

    out: list = []
    for c in links:
        ch = c.get("channel")
        freq = channel_to_freq(ch)
        out.append(
            WifiLinkInfo(
                interface=c.get("interface"),
                ssid=c.get("ssid"),
                bssid=c.get("bssid"),
                signal_pct=c.get("signal_pct"),
                channel=ch,
                radio_type=c.get("radio_type"),
                receive_rate_mbps=c.get("receive_rate_mbps"),
                transmit_rate_mbps=c.get("transmit_rate_mbps"),
                frequency_mhz=freq,
                band=_band(freq),
                security=c.get("security"),
                state=c.get("state"),
                source="wlan",
            )
        )
    return out


def parse_wlan_networks(text: str) -> list:
    """`netsh wlan show networks mode=bssid` -> visible AP/SSID list (metadata only)."""
    networks: list = []
    current_ap: Optional[dict] = None
    current_bssid: Optional[dict] = None
    iface = None
    for raw in text.splitlines():
        line = raw.rstrip()
        key, value = _kv(line)
        if key is None:
            continue
        if key in ("interface name", "interface"):
            iface = value
            continue
        if key.startswith("ssid") and not key.startswith("bssid"):
            current_ap = {"ssid": value, "interface": iface, "bssids": []}
            networks.append(current_ap)
            current_bssid = None
            continue
        if current_ap is None:
            continue
        if key.startswith("bssid"):
            current_bssid = {"bssid": normalize_mac(value)}
            current_ap["bssids"].append(current_bssid)
            continue
        target = current_bssid if current_bssid is not None else current_ap
        if key == "network type":
            target["network_type"] = value
        elif key == "authentication":
            target["authentication"] = value
        elif key == "encryption":
            target["encryption"] = value
        elif key == "signal":
            target["signal_pct"] = _to_int(value)
        elif key == "radio type":
            target["radio_type"] = value
        elif key == "channel":
            target["channel"] = _to_int(value)
    return networks


# ---------------------------------------------------------------- interfaces


def parse_netsh_admin_interfaces(text: str) -> list:
    """`netsh interface show interface` -> [name, admin_state, state, type]."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("admin state") or set(line) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 4:
            rows.append(
                {
                    "admin_state": parts[0].lower(),
                    "state": parts[1].lower(),
                    "type": parts[2].lower(),
                    "name": " ".join(parts[3:]),
                }
            )
    return rows


def parse_netsh_ip_interfaces(text: str) -> list:
    """`netsh interface ipv4 show interfaces` -> idx/met/mtu/state/name rows."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("idx") or set(line) <= {"-", " "}:
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 5 and parts[0].isdigit():
            rows.append(
                {
                    "idx": int(parts[0]),
                    "metric": _to_int(parts[1]),
                    "mtu": _to_int(parts[2]),
                    "state": parts[3].lower(),
                    "name": " ".join(parts[4:]),
                }
            )
    return rows


def parse_netsh_firewall_profile(text: str) -> dict:
    """`netsh advfirewall show currentprofile` -> {state, policy...}.

    Format is 'Key   Value' (no colon) under the first section header.
    """
    out: dict = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-", "="}:
            continue
        if line.endswith(":"):
            section = line[:-1].strip().lower()
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) != 2:
            continue
        key, value = parts[0].strip().lower(), parts[1].strip()
        if section and section != "current profile settings":
            out[f"{section}.{key}"] = value
            continue
        if key == "state":
            out["state"] = value.upper()
        elif key == "firewall policy":
            out["policy"] = value
    out["enabled"] = out.get("state") == "ON"
    return out
