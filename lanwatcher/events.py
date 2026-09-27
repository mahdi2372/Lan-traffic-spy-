"""In-process event bus connecting collectors/services to the UI (SSE bridge).

Thread-safe publish/subscribe. Subscribers run inline; slow subscribers should
queue internally. A failing subscriber never breaks publishers or peers.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("lanwatcher.events")

# Canonical topics
TOPIC_DEVICE_NEW = "device.new"
TOPIC_DEVICE_UPDATED = "device.updated"
TOPIC_DEVICE_ONLINE = "device.online"
TOPIC_DEVICE_OFFLINE = "device.offline"
TOPIC_ALERT_NEW = "alert.new"
TOPIC_ALERT_UPDATED = "alert.updated"
TOPIC_HEALTH = "health"
TOPIC_FLOWS = "flows"
TOPIC_STATS = "stats"
TOPIC_ROUTER = "router"
TOPIC_INTERFACES = "interfaces"
TOPIC_WIFI = "wifi"
TOPIC_SCAN_STARTED = "scan.started"
TOPIC_SCAN_FINISHED = "scan.finished"
TOPIC_COLLECTOR_STATUS = "collector.status"
TOPIC_ERROR = "system.error"


@dataclass
class Event:
    topic: str
    payload: Any
    ts: str = ""
    seq: int = 0
    meta: dict = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict = {}
        self._seq = 0

    def subscribe(self, topic: str, fn: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to a topic or '*' for everything. Returns an unsubscribe callable."""
        with self._lock:
            self._subs.setdefault(topic, []).append(fn)

        def _unsub() -> None:
            with self._lock:
                lst = self._subs.get(topic, [])
                if fn in lst:
                    lst.remove(fn)

        return _unsub

    def publish(self, topic: str, payload: Any, meta: Optional[dict] = None) -> Event:
        from .util import iso

        with self._lock:
            self._seq += 1
            ev = Event(topic=topic, payload=payload, ts=iso(), seq=self._seq, meta=meta or {})
            targets = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for fn in targets:
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 - subscriber isolation
                log.exception("event subscriber failed for topic %s", topic)
        return ev
