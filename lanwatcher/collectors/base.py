"""Collector base types: every discovery source implements Collector.

Design rules:
- collect() must never raise: any failure is captured in CollectorResult
- each collector fails independently without crashing the application
- collectors produce *observations* (with source + confidence), never mutate storage
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import Config
from ..events import EventBus
from ..models import (
    DeviceObservation,
    FlowObservation,
    InterfaceInfo,
    NetworkEvent,
    RouterSnapshot,
    WifiLinkInfo,
)
from ..util import CommandRunner, iso

log = logging.getLogger("lanwatcher.collectors")


@dataclass
class CollectorContext:
    config: Config
    runner: CommandRunner
    oui: Any = None
    bus: Optional[EventBus] = None
    is_windows: bool = False
    is_macos: bool = False
    is_elevated: bool = False
    data_dir: Any = None
    state: dict = field(default_factory=dict)  # shared scratch (e.g. gateway ip)


@dataclass
class CollectorResult:
    collector: str
    ok: bool = True
    observations: list = field(default_factory=list)
    interfaces: list = field(default_factory=list)
    wifi_links: list = field(default_factory=list)
    visible_networks: list = field(default_factory=list)
    flows: list = field(default_factory=list)
    router: Optional[RouterSnapshot] = None
    events: list = field(default_factory=list)
    health: Any = None
    system: dict = field(default_factory=dict)
    unavailable_reason: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0

    def merge(self, other: "CollectorResult") -> "CollectorResult":
        self.observations.extend(other.observations)
        self.interfaces.extend(other.interfaces)
        self.wifi_links.extend(other.wifi_links)
        self.visible_networks.extend(other.visible_networks)
        self.flows.extend(other.flows)
        self.events.extend(other.events)
        if other.router is not None:
            if self.router is None:
                self.router = other.router
            else:
                for k, v in other.router.fields.items():
                    if k == "_sources":
                        self.router.fields.setdefault("_sources", {}).update(v)
                    elif k not in self.router.fields:
                        self.router.fields[k] = v
        if other.health is not None:
            self.health = other.health
        self.system.update(other.system)
        return self


@dataclass
class CollectorStatus:
    name: str
    ok: bool = True
    runs: int = 0
    errors: int = 0
    last_run: Optional[str] = None
    last_error: Optional[str] = None
    last_duration_ms: float = 0.0
    last_unavailable: Optional[str] = None
    detail: str = ""


class Collector(ABC):
    name: str = "collector"
    description: str = ""
    interval_s: float = 30.0
    requires_elevation: bool = False

    def __init__(self, ctx: CollectorContext):
        self.ctx = ctx

    @abstractmethod
    def collect(self) -> CollectorResult:
        """Gather data. Implementations must catch their own expected errors;
        the registry still guards against unexpected exceptions."""

    def available(self) -> tuple:
        """(is_available, reason_if_not)."""
        return True, None


class CollectorRegistry:
    """Runs collectors with strict isolation and tracks per-collector status."""

    def __init__(self, collectors: list, bus: Optional[EventBus] = None):
        self.collectors = list(collectors)
        self.bus = bus
        self.status: dict = {c.name: CollectorStatus(name=c.name, detail=c.description) for c in self.collectors}

    def names(self) -> list:
        return [c.name for c in self.collectors]

    def run_one(self, collector: Collector) -> CollectorResult:
        st = self.status.setdefault(collector.name, CollectorStatus(name=collector.name))
        t0 = time.perf_counter()
        try:
            avail, reason = collector.available()
            if not avail:
                result = CollectorResult(collector=collector.name, ok=False, unavailable_reason=reason)
            else:
                result = collector.collect()
                result.collector = collector.name
        except Exception as exc:  # noqa: BLE001 - strict isolation
            log.exception("collector %s crashed", collector.name)
            result = CollectorResult(collector=collector.name, ok=False, error=f"{type(exc).__name__}: {exc}")
        result.duration_ms = (time.perf_counter() - t0) * 1000.0
        st.runs += 1
        st.last_run = iso()
        st.last_duration_ms = result.duration_ms
        st.last_unavailable = result.unavailable_reason
        if result.error or not result.ok:
            st.errors += 1
            st.ok = False
            st.last_error = result.error or result.unavailable_reason
        else:
            st.ok = True
            st.last_error = None
        if self.bus is not None:
            self.bus.publish(
                "collector.status",
                {
                    "name": collector.name,
                    "ok": st.ok,
                    "error": st.last_error,
                    "unavailable": result.unavailable_reason,
                    "duration_ms": result.duration_ms,
                },
            )
        return result

    def run_all(self) -> CollectorResult:
        merged = CollectorResult(collector="*")
        for c in self.collectors:
            merged.merge(self.run_one(c))
        return merged

    def statuses(self) -> list:
        return [self.status[c.name] for c in self.collectors]
