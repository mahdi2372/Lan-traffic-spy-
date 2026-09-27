"""Alert service: new-device detection + configurable alert rules + triage.

When an unknown device appears: detect -> compare with inventory -> alert with
IP/MAC/vendor/time -> operator marks trusted / unknown / ignore / investigate.
LAN Watcher NEVER attacks, disconnects or blocks devices automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..db import Repositories
from ..events import TOPIC_ALERT_NEW, TOPIC_ALERT_UPDATED, EventBus
from ..models import (
    TRUST_IGNORE,
    TRUST_INVESTIGATE,
    TRUST_TRUSTED,
    TRUST_UNKNOWN,
    Alert,
    TRUST_STATES,
)
from ..util import iso

log = logging.getLogger("lanwatcher.alerts")


class AlertService:
    def __init__(self, repos: Repositories, bus: EventBus, config):
        self.repos = repos
        self.bus = bus
        self.config = config
        self._known_ids: set = {r["device_id"] for r in repos.devices.list_all()}

    # ============================================================ detection

    def on_new_device(self, device_id: str) -> Optional[Alert]:
        """Called by discovery events. Emits alert when policy enabled."""
        device = self.repos.devices.get(device_id)
        if not device:
            return None
        if device.get("is_self"):
            self._known_ids.add(device_id)
            return None
        is_known = device_id in self._known_ids
        self._known_ids.add(device_id)
        if is_known:
            return None
        if not self.config.get("alerts.new_device", True):
            return None

        existing = self.repos.alerts.active_new_device_alert_for(device_id)
        if existing:
            return None

        alert = Alert(
            kind="new_device",
            severity="warning",
            message=f"New device appeared: {device.get('friendly_name') or device.get('hostname') or device.get('ipv4') or device.get('mac') or 'unknown'}",
            device_id=device_id,
            details={
                "ip": device.get("ipv4"),
                "ipv6": device.get("ipv6"),
                "mac": device.get("mac"),
                "hostname": device.get("hostname"),
                "vendor": device.get("vendor"),
                "connection_type": device.get("connection_type"),
                "first_seen": device.get("first_seen"),
                "source": "new-device-detector",
            },
            state=TRUST_UNKNOWN,
        )
        alert.alert_id = self.repos.alerts.add(alert)
        self.bus.publish(TOPIC_ALERT_NEW, alert.to_dict())
        return alert

    def on_device_event(self, event_type: str, device_id: str, details: Optional[dict] = None) -> Optional[Alert]:
        details = details or {}
        kind_map = {"offline": "device_offline", "online": "device_online", "ip_change": "ip_change"}
        kind = kind_map.get(event_type)
        if not kind:
            return None
        if not self.config.get(f"alerts.{kind}", False):
            return None
        device = self.repos.devices.get(device_id) or {}
        messages = {
            "device_offline": f"Device went offline: {device.get('hostname') or device.get('ipv4') or device_id}",
            "device_online": f"Device back online: {device.get('hostname') or device.get('ipv4') or device_id}",
            "ip_change": f"IP changed for {device.get('hostname') or device.get('mac') or device_id}: {details.get('old')} -> {details.get('new')}",
        }
        alert = Alert(
            kind=kind,
            severity="info",
            message=messages[kind],
            device_id=device_id,
            details=details,
            state=TRUST_UNKNOWN,
        )
        alert.alert_id = self.repos.alerts.add(alert)
        self.bus.publish(TOPIC_ALERT_NEW, alert.to_dict())
        return alert

    def on_health(self, health: dict) -> None:
        """Health degradations are informational; no automatic actions ever."""
        overall = health.get("overall")
        if overall not in ("degraded", "critical"):
            return
        recent = self.repos.alerts.list(kind="health", since=iso()[:10], limit=1)
        if recent:
            return
        alert = Alert(
            kind="health",
            severity="warning" if overall == "degraded" else "critical",
            message=f"Network health is {overall} (score {health.get('score')})",
            details={"overall": overall, "score": health.get("score")},
            state=TRUST_UNKNOWN,
        )
        alert.alert_id = self.repos.alerts.add(alert)
        self.bus.publish(TOPIC_ALERT_NEW, alert.to_dict())

    # ============================================================ triage

    def triage(self, alert_id: int, state: str) -> Optional[dict]:
        if state not in TRUST_STATES:
            return None
        self.repos.alerts.set_state(alert_id, state)
        rows = self.repos.alerts.list(limit=1000)
        alert = next((a for a in rows if a["id"] == alert_id), None)
        if alert and alert.get("device_id"):
            # Reflect triage onto the device inventory trust state.
            if state in (TRUST_TRUSTED, TRUST_IGNORE, TRUST_INVESTIGATE):
                self.repos.devices.update_fields(alert["device_id"], {"trust_state": state})
            if state in (TRUST_TRUSTED, TRUST_IGNORE):
                self.repos.alerts.resolve(alert_id)
        self.bus.publish(TOPIC_ALERT_UPDATED, {"alert_id": alert_id, "state": state})
        return alert

    def list(self, state: Optional[str] = None, kind: Optional[str] = None, since: Optional[str] = None, limit: int = 500) -> list:
        return self.repos.alerts.list(state=state, kind=kind, since=since, limit=limit)
