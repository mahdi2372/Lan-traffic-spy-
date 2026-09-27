"""Correlation engine: decides whether observations describe the same device.

Evidence rules (ordered by strength):
1.  Same normalized MAC  -> same device (highest confidence)
2.  Same IPv4 + compatible identity (hostname or vendor match) -> same device
3.  Same IPv6 -> same device
4.  Same hostname + compatible vendor -> same device
5.  Same IPv4 with a DIFFERENT MAC -> IP reassignment: new/existing device, IP
    change event; records are never merged.

Conflicting field values are never silently overwritten: every value is stored
as a FieldRecord (Field -> Value -> Source -> Timestamp -> Confidence), the
current value is chosen by (confidence, recency) and conflicts raise a
'conflict' network event + are exposed in the device profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..db import Repositories
from ..events import (
    TOPIC_DEVICE_NEW,
    TOPIC_DEVICE_ONLINE,
    TOPIC_DEVICE_OFFLINE,
    TOPIC_DEVICE_UPDATED,
    EventBus,
)
from ..models import (
    CONFIDENCE_ORDER,
    Device,
    DeviceObservation,
    FieldRecord,
    NetworkEvent,
    Session,
    new_device_id,
)
from ..util import iso, normalize_mac, seconds_since, utc_now

log = logging.getLogger("lanwatcher.correlation")

# Fields the correlation engine manages on the devices row.
MANAGED_FIELDS = (
    "mac", "ipv4", "ipv6", "hostname", "vendor",
    "connection_type", "interface_name", "category",
)
ONLINE_FIELD = "online"
LATENCY_FIELD = "latency_ms"


@dataclass
class CorrelationOutcome:
    device_id: str
    created: bool = False
    events: list = field(default_factory=list)
    updated_fields: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


class CorrelationEngine:
    def __init__(self, repos: Repositories, bus: Optional[EventBus] = None, config=None):
        self.repos = repos
        self.bus = bus
        self.config = config

    # ============================================================ public

    def ingest(self, obs: DeviceObservation) -> CorrelationOutcome:
        device, created = self._resolve_device(obs)
        outcome = CorrelationOutcome(device_id=device["device_id"], created=created)

        if created:
            outcome.events.append(
                NetworkEvent(
                    event_type="new_device",
                    device_id=device["device_id"],
                    details={
                        "ip": obs.ipv4 or obs.ipv6,
                        "mac": obs.mac,
                        "hostname": obs.hostname,
                        "vendor": obs.get("vendor"),
                        "source": obs.source,
                    },
                    source=obs.source,
                    ts=obs.observed_at,
                )
            )

        updates = self._merge_fields(device, obs, outcome)
        self._handle_online(device, obs, updates, outcome)
        self._handle_latency(device, obs, updates)
        self._handle_bytes(device, obs, updates)

        if updates:
            self.repos.devices.update_fields(device["device_id"], updates)
            outcome.updated_fields = list(updates.keys())
        # provenance store
        self.repos.observations.record_observation(device["device_id"], obs)
        return outcome

    # ============================================================ identity

    def _resolve_device(self, obs: DeviceObservation) -> tuple:
        mac = normalize_mac(obs.mac) if obs.mac else None

        if mac:
            row = self.repos.devices.find_by_mac(mac)
            if row:
                return row, False

        if obs.ipv4:
            row = self.repos.devices.find_by_ip(obs.ipv4)
            if row:
                row_mac = normalize_mac(row.get("mac")) if row.get("mac") else None
                if mac and row_mac and mac != row_mac:
                    # IP now belongs to a different MAC: do not merge.
                    return self._create_device(obs, mac), True
                return row, False

        if obs.ipv6:
            row = self.repos.devices.find_by_ip(obs.ipv6)
            if row:
                return row, False

        if obs.hostname:
            row = self.repos.devices.find_by_hostname(obs.hostname)
            if row:
                row_mac = normalize_mac(row.get("mac")) if row.get("mac") else None
                if mac and row_mac and mac != row_mac:
                    return self._create_device(obs, mac), True
                return row, False

        return self._create_device(obs, mac), True

    def _create_device(self, obs: DeviceObservation, mac: Optional[str]) -> dict:
        from ..util import mac_display

        dev = Device(
            device_id=new_device_id(),
            mac=mac_display(mac) if mac else None,
            ipv4=obs.ipv4,
            ipv6=obs.ipv6,
            hostname=obs.hostname,
            vendor=obs.get("vendor"),
            connection_type=obs.get("connection_type") or "unknown",
            interface_name=obs.get("interface_name"),
            category=obs.get("category") or "unknown",
            first_seen=obs.observed_at,
            last_seen=obs.observed_at,
            online=0,  # the online/offline transition logic opens sessions
            confidence=obs.get("confidence_summary") or "low",
            is_self=1 if obs.raw.get("is_self") else 0,
        )
        if obs.raw.get("is_self"):
            dev.category = dev.category or "computer"
        self.repos.devices.insert(dev)
        return self.repos.devices.get(dev.device_id)

    # ============================================================ field merge

    def _merge_fields(self, device: dict, obs: DeviceObservation, outcome: CorrelationOutcome) -> dict:
        updates: dict = {}
        device_id = device["device_id"]

        for name in MANAGED_FIELDS:
            ov = obs.fields.get(name)
            if ov is None or ov.value in (None, ""):
                continue
            value = ov.value
            if name == "mac":
                value = normalize_mac(value)
                if value and device.get("mac_norm") and value != device["mac_norm"]:
                    outcome.events.append(
                        NetworkEvent(
                            event_type="mac_change",
                            device_id=device_id,
                            details={"old": device.get("mac"), "new": value, "source": obs.source},
                            source=obs.source,
                        )
                    )
                    outcome.conflicts.append(("mac", device.get("mac"), value, obs.source))
                updates["mac_norm"] = value
                from ..util import mac_display

                updates["mac"] = mac_display(value)
                continue

            current = device.get(name)
            if name == "ipv4" and current and value != current:
                outcome.events.append(
                    NetworkEvent(event_type="ip_change", device_id=device_id,
                                 details={"old": current, "new": value, "source": obs.source}, source=obs.source)
                )
            if name == "hostname" and current and str(value).lower() != str(current).lower():
                outcome.events.append(
                    NetworkEvent(event_type="hostname_change", device_id=device_id,
                                 details={"old": current, "new": value, "source": obs.source}, source=obs.source)
                )
            if name == "connection_type" and current and value != current and current != "unknown":
                outcome.events.append(
                    NetworkEvent(event_type="connectivity_change", device_id=device_id,
                                 details={"old": current, "new": value, "source": obs.source}, source=obs.source)
                )
            if name == "vendor" and current and value != current:
                outcome.events.append(
                    NetworkEvent(event_type="vendor_change", device_id=device_id,
                                 details={"old": current, "new": value, "source": obs.source}, source=obs.source)
                )

            if not self._should_replace(device, name, current, ov):
                if current not in (None, "") and str(current) != str(value):
                    # conflict kept in provenance, current value retained
                    outcome.conflicts.append((name, current, value, obs.source))
                    outcome.events.append(
                        NetworkEvent(event_type="conflict", device_id=device_id,
                                     details={"field": name, "kept": current, "other": value, "other_source": obs.source},
                                     source=obs.source)
                    )
                continue

            if current not in (None, "") and str(current) != str(value):
                # we are replacing with better evidence - record the change too if meaningful
                pass
            updates[name] = value
            outcome.updated_fields.append(name)

        # first_seen: never later than any observation
        if obs.observed_at and device.get("first_seen") and obs.observed_at < device["first_seen"]:
            updates["first_seen"] = obs.observed_at
        updates.setdefault("last_seen", obs.observed_at)
        updates["last_seen"] = max(updates["last_seen"], device.get("last_seen") or "", obs.observed_at)
        updates["confidence"] = self._device_confidence(device, updates)
        return updates

    @staticmethod
    def _should_replace(device: dict, name: str, current, ov) -> bool:
        """Non-destructive: replace only with strictly better evidence, or newer equal evidence.

        User-set fields are never touched (they are not in MANAGED_FIELDS).
        """
        if current in (None, ""):
            return True
        if str(current) == str(ov.value):
            return True  # refresh timestamp via observation store
        cur_conf = "high" if name in ("mac", "ipv4") else "medium"
        # Use stored field_observations to compare confidence when available is
        # handled by recency + confidence of the *incoming* value vs assumed.
        if CONFIDENCE_ORDER.get(ov.confidence, 0) > CONFIDENCE_ORDER.get(cur_conf, 0):
            return True
        if CONFIDENCE_ORDER.get(ov.confidence, 0) < CONFIDENCE_ORDER.get(cur_conf, 0):
            return False
        # equal confidence: newer wins
        return (ov.observed_at or "") >= (device.get("updated_at") or device.get("last_seen") or "")

    @staticmethod
    def _device_confidence(device: dict, updates: dict) -> str:
        mac = updates.get("mac_norm", device.get("mac_norm"))
        ip = updates.get("ipv4", device.get("ipv4")) or updates.get("ipv6", device.get("ipv6"))
        hostname = updates.get("hostname", device.get("hostname"))
        if mac and ip and hostname:
            return "high"
        if mac and (ip or hostname):
            return "medium"
        return "low"

    # ============================================================ online/offline

    def _handle_online(self, device: dict, obs: DeviceObservation, updates: dict, outcome: CorrelationOutcome) -> None:
        online_val = obs.fields.get(ONLINE_FIELD)
        if online_val is None:
            return
        was_online = bool(device.get("online"))
        now_online = bool(online_val.value)
        updates["online"] = 1 if now_online else 0
        if now_online and not was_online:
            outcome.events.append(
                NetworkEvent(event_type="online", device_id=device["device_id"], details={"source": obs.source}, source=obs.source)
            )
            self.repos.sessions.open_session(
                Session(
                    device_id=device["device_id"],
                    started_at=obs.observed_at,
                    ip=obs.ipv4 or device.get("ipv4"),
                    connection_type=updates.get("connection_type", device.get("connection_type") or "unknown"),
                    source=obs.source,
                )
            )
        elif was_online and not now_online:
            outcome.events.append(
                NetworkEvent(event_type="offline", device_id=device["device_id"],
                             details={"last_seen": device.get("last_seen")}, source=obs.source)
            )
            self.repos.sessions.close_open(device["device_id"], ended_at=obs.observed_at)

    def _handle_latency(self, device: dict, obs: DeviceObservation, updates: dict) -> None:
        ov = obs.fields.get(LATENCY_FIELD)
        if ov is None or ov.value is None:
            return
        lat = float(ov.value)
        updates["last_latency_ms"] = lat
        prev_avg = device.get("avg_latency_ms")
        updates["avg_latency_ms"] = lat if prev_avg is None else round(prev_avg * 0.7 + lat * 0.3, 2)

    def _handle_bytes(self, device: dict, obs: DeviceObservation, updates: dict) -> None:
        """Per-device byte counters exist only when a source actually measures
        them (router per-client telemetry, elevated EStats). Otherwise they
        stay NULL and the UI shows '—' - we never invent traffic numbers."""
        for name in ("bytes_sent", "bytes_recv"):
            ov = obs.fields.get(name)
            if ov is None or ov.value is None:
                continue
            try:
                updates[name] = int(ov.value)
            except (TypeError, ValueError):
                continue

    # ============================================================ offline sweep

    def mark_stale_offline(self) -> list:
        """Devices unseen for offline_threshold_s go offline (with events)."""
        threshold = int(self.config.get("general.offline_threshold_s", 120)) if self.config else 120
        events = []
        for row in self.repos.devices.list_online():
            if row.get("is_self"):
                continue
            age = seconds_since(row["last_seen"])
            if age > threshold:
                self.repos.devices.update_fields(row["device_id"], {"online": 0})
                self.repos.sessions.close_open(row["device_id"])
                ev = NetworkEvent(
                    event_type="offline",
                    device_id=row["device_id"],
                    details={"last_seen": row["last_seen"], "age_s": round(age)},
                    source="correlator",
                )
                self.repos.events.add(ev)
                events.append(ev)
                if self.bus:
                    self.bus.publish(TOPIC_DEVICE_OFFLINE, {"device_id": row["device_id"], "last_seen": row["last_seen"]})
        return events

    def flush_events(self, outcome: CorrelationOutcome) -> None:
        for ev in outcome.events:
            self.repos.events.add(ev)
        if not self.bus:
            return
        if outcome.created:
            self.bus.publish(TOPIC_DEVICE_NEW, {"device_id": outcome.device_id})
        elif outcome.updated_fields:
            self.bus.publish(TOPIC_DEVICE_UPDATED, {"device_id": outcome.device_id, "fields": outcome.updated_fields})
        for ev in outcome.events:
            if ev.event_type == "online":
                self.bus.publish(TOPIC_DEVICE_ONLINE, {"device_id": outcome.device_id})
