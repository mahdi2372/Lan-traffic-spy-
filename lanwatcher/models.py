"""Domain model dataclasses shared across collectors, services, database and UI.

Every value carries optional provenance (source + confidence) so the application
can show "Field -> Value -> Source -> Timestamp -> Confidence" as required for
the correlation engine. Conflicts are never silently overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .util import iso, normalize_mac

# Confidence of a detected value given its source.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_ORDER = {CONFIDENCE_LOW: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_HIGH: 2}

# Connection classification. 'unknown' is a first-class value: we never claim
# wired/wireless without evidence.
CONN_WIFI = "wifi"
CONN_ETHERNET = "ethernet"
CONN_UNKNOWN = "unknown"

TRUST_UNKNOWN = "unknown"
TRUST_TRUSTED = "trusted"
TRUST_IGNORE = "ignore"
TRUST_INVESTIGATE = "investigate"
TRUST_STATES = (TRUST_UNKNOWN, TRUST_TRUSTED, TRUST_IGNORE, TRUST_INVESTIGATE)


def new_device_id() -> str:
    return "dev_" + uuid.uuid4().hex[:12]


@dataclass
class ObservedValue:
    """One field value as reported by one source at one time."""

    value: Any
    source: str
    confidence: str = CONFIDENCE_MEDIUM
    observed_at: str = field(default_factory=iso)

    def as_row(self, device_id: str, field_name: str) -> tuple:
        return (device_id, field_name, _to_text(self.value), self.source, self.observed_at, self.confidence)


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


@dataclass
class DeviceObservation:
    """A collector's view of one device at one instant.

    `fields` maps attribute name -> ObservedValue. Standard attribute names:
    mac, ipv4, ipv6, hostname, vendor, connection_type, interface_name,
    category, latency_ms, bytes_sent, bytes_recv, online.
    """

    source: str
    fields: dict = field(default_factory=dict)
    observed_at: str = field(default_factory=iso)
    raw: dict = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        ov = self.fields.get(name)
        return ov.value if ov is not None else default

    def set(self, name: str, value: Any, confidence: str = CONFIDENCE_MEDIUM) -> None:
        if value is None or value == "":
            return
        if name == "mac":
            value = normalize_mac(value) or value
        self.fields[name] = ObservedValue(value=value, source=self.source, confidence=confidence, observed_at=self.observed_at)

    @property
    def mac(self) -> Optional[str]:
        return self.get("mac")

    @property
    def ipv4(self) -> Optional[str]:
        return self.get("ipv4")

    @property
    def ipv6(self) -> Optional[str]:
        return self.get("ipv6")

    @property
    def hostname(self) -> Optional[str]:
        return self.get("hostname")


@dataclass
class Device:
    """Current, correlated view of a device (devices table row)."""

    device_id: str
    mac: Optional[str] = None
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    connection_type: str = CONN_UNKNOWN
    interface_name: Optional[str] = None
    category: str = "unknown"
    friendly_name: Optional[str] = None
    notes: Optional[str] = None
    notes_encrypted: int = 0
    first_seen: str = field(default_factory=iso)
    last_seen: str = field(default_factory=iso)
    online: int = 0
    last_latency_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    probes_sent: int = 0
    probes_lost: int = 0
    bytes_sent: Optional[int] = None
    bytes_recv: Optional[int] = None
    pinned: int = 0
    trust_state: str = TRUST_UNKNOWN
    confidence: str = CONFIDENCE_LOW
    is_self: int = 0
    source_summary: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FieldRecord:
    """Row of field_observations: Field -> Value -> Source -> Timestamp -> Confidence."""

    field: str
    value: Optional[str]
    source: str
    observed_at: str
    confidence: str = CONFIDENCE_MEDIUM
    is_current: int = 1

    def as_tuple(self) -> tuple:
        return (self.field, self.value, self.source, self.observed_at, self.confidence, self.is_current)


@dataclass
class NetworkEvent:
    event_type: str  # new_device | online | offline | ip_change | mac_change | hostname_change | connectivity_change | vendor_change | conflict
    device_id: Optional[str]
    details: dict = field(default_factory=dict)
    source: str = "system"
    ts: str = field(default_factory=iso)
    event_id: Optional[int] = None

    def describe(self) -> str:
        d = self.details or {}
        if self.event_type == "new_device":
            return f"New device discovered ({d.get('ip') or d.get('mac') or 'unknown identity'})"
        if self.event_type == "ip_change":
            return f"IP address changed: {d.get('old') or '?'} -> {d.get('new') or '?'}"
        if self.event_type == "mac_change":
            return f"MAC address changed: {d.get('old') or '?'} -> {d.get('new') or '?'}"
        if self.event_type == "hostname_change":
            return f"Hostname changed: {d.get('old') or '?'} -> {d.get('new') or '?'}"
        if self.event_type == "online":
            return "Device came online"
        if self.event_type == "offline":
            return f"Device went offline (last seen {d.get('last_seen') or 'unknown'})"
        if self.event_type == "connectivity_change":
            return f"Connectivity changed: {d.get('old') or '?'} -> {d.get('new') or '?'}"
        if self.event_type == "vendor_change":
            return f"Vendor changed: {d.get('old') or '?'} -> {d.get('new') or '?'}"
        if self.event_type == "conflict":
            return f"Conflicting {d.get('field')} values: {d.get('kept')} (kept) vs {d.get('other')} ({d.get('other_source')})"
        return self.event_type


@dataclass
class Session:
    device_id: str
    started_at: str
    ended_at: Optional[str] = None
    ip: Optional[str] = None
    connection_type: str = CONN_UNKNOWN
    source: str = "system"
    session_id: Optional[int] = None


@dataclass
class Alert:
    kind: str  # new_device | device_offline | device_online | ip_change | health
    severity: str  # info | warning | critical
    message: str
    device_id: Optional[str] = None
    details: dict = field(default_factory=dict)
    state: str = TRUST_UNKNOWN  # triage: unknown | trusted | ignore | investigate
    ts: str = field(default_factory=iso)
    resolved_at: Optional[str] = None
    alert_id: Optional[int] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FlowObservation:
    """Privacy-preserving flow metadata. Never contains payload data."""

    src_ip: Optional[str]
    dst_ip: Optional[str]
    src_port: Optional[int]
    dst_port: Optional[int]
    protocol: str  # tcp | udp | icmp | other
    ts: str = field(default_factory=iso)
    bytes: Optional[int] = None
    duration_s: Optional[float] = None
    source: str = "flow"
    device_id: Optional[str] = None
    state: Optional[str] = None


@dataclass
class InterfaceInfo:
    name: str
    description: Optional[str] = None
    mac: Optional[str] = None
    kind: str = CONN_UNKNOWN  # wifi | ethernet | other | unknown
    is_up: int = 0
    link_speed_bps: Optional[int] = None
    mtu: Optional[int] = None
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv6: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None  # comma separated
    profile: Optional[str] = None  # network profile name (Windows)
    source: str = "system"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class WifiLinkInfo:
    """Wi-Fi association details. Never includes or attempts to obtain passwords."""

    interface: Optional[str] = None
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    signal_pct: Optional[int] = None
    channel: Optional[int] = None
    radio_type: Optional[str] = None
    receive_rate_mbps: Optional[float] = None
    transmit_rate_mbps: Optional[float] = None
    frequency_mhz: Optional[int] = None
    band: Optional[str] = None
    security: Optional[str] = None
    state: Optional[str] = None
    source: str = "wlan"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class HealthComponent:
    name: str
    status: str  # ok | degraded | down | unknown | unavailable
    detail: str = ""
    explanation: str = ""
    latency_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    measured: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class HealthSnapshot:
    ts: str = field(default_factory=iso)
    overall: str = "unknown"
    score: int = 0
    components: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "overall": self.overall,
            "score": self.score,
            "components": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.components],
        }


@dataclass
class RouterSnapshot:
    """Normalized router information gathered through authorized mechanisms
    (UPnP IGD, operator-configured API, local interface data)."""

    ts: str = field(default_factory=iso)
    source: str = "router"
    fields: dict = field(default_factory=dict)

    # canonical field names:
    # router_ip, gateway_ip, hostname, lan_subnet, dhcp_range_start, dhcp_range_end,
    # wan_ip, wan_status, wan_uptime_s, model, vendor, dns_servers, ipv6_gateway,
    # ipv6_wan, uptime_s, connected_clients, dhcp_leases (list of dicts), lease_time_s

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def set(self, key: str, value: Any, source: Optional[str] = None) -> None:
        if value is None or value == "":
            return
        self.fields[key] = value
        if source:
            self.fields.setdefault("_sources", {})[key] = source
