"""Discovery service: orchestrates collectors -> correlator -> database -> events.

Async workers with rate-limited probing, batching and graceful cancellation.
The scheduler runs collectors at their own cadence and never floods the network.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..collectors.base import CollectorContext, CollectorRegistry, CollectorResult
from ..config import Config
from ..db import Repositories
from ..events import (
    TOPIC_COLLECTOR_STATUS,
    TOPIC_FLOWS,
    TOPIC_HEALTH,
    TOPIC_INTERFACES,
    TOPIC_ROUTER,
    TOPIC_SCAN_FINISHED,
    TOPIC_SCAN_STARTED,
    TOPIC_STATS,
    TOPIC_WIFI,
    EventBus,
)
from ..models import FlowObservation
from ..services.correlation import CorrelationEngine
from ..services.ping import Pinger
from ..util import epoch_to_iso, iso, utc_now

log = logging.getLogger("lanwatcher.discovery")


class DiscoveryService:
    def __init__(
        self,
        config: Config,
        repos: Repositories,
        registry: CollectorRegistry,
        bus: EventBus,
        pinger: Optional[Pinger] = None,
    ):
        self.config = config
        self.repos = repos
        self.registry = registry
        self.bus = bus
        self.correlator = CorrelationEngine(repos, bus, config)
        self.pinger = pinger or Pinger()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._scan_lock = threading.Lock()
        self._last_run: dict = {}
        self.scan_count = 0
        self.last_scan_ms = 0.0

    # ============================================================ lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lanwatcher-discovery", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        last_full = 0.0
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001 - worker must survive
                log.exception("discovery scan failed")
            self.last_scan_ms = (time.perf_counter() - t0) * 1000.0
            interval = self.config.get("general.scan_interval_s", 30)
            # sleep in small increments for graceful cancellation
            deadline = time.monotonic() + interval
            while not self._stop.is_set() and time.monotonic() < deadline:
                self._stop.wait(0.2)

    # ============================================================ one scan

    def scan_once(self) -> CollectorResult:
        with self._scan_lock:
            self.scan_count += 1
            self.bus.publish(TOPIC_SCAN_STARTED, {"scan": self.scan_count})
            now = time.monotonic()
            merged = CollectorResult(collector="*")

            for collector in self.registry.collectors:
                last = self._last_run.get(collector.name, 0.0)
                if now - last < collector.interval_s * 0.95 and collector.name not in ("arp", "neighbors", "flow"):
                    continue
                result = self.registry.run_one(collector)
                self._last_run[collector.name] = now
                merged.merge(result)
                self._process_result(result)

            # offline sweep + ping probing
            self._probe_devices()
            self.correlator.mark_stale_offline()
            self._publish_stats()
            self.bus.publish(TOPIC_SCAN_FINISHED, {"scan": self.scan_count, "ms": self.last_scan_ms})
            return merged

    def _process_result(self, result: CollectorResult) -> None:
        # device observations -> correlator (batched)
        for obs in result.observations:
            try:
                outcome = self.correlator.ingest(obs)
                self.correlator.flush_events(outcome)
            except Exception:  # noqa: BLE001
                log.exception("failed to ingest observation from %s", result.collector)

        if result.interfaces:
            try:
                self.repos.interfaces.save_batch(result.interfaces)
                # keep context state for other collectors
                for collector in self.registry.collectors:
                    if hasattr(collector, "ctx"):
                        collector.ctx.state.setdefault("interfaces", result.interfaces)
                self.bus.publish(TOPIC_INTERFACES, {"count": len(result.interfaces)})
            except Exception:  # noqa: BLE001
                log.exception("failed to store interfaces")

        if result.wifi_links:
            try:
                self.repos.wifi.save(result.wifi_links)
                self.bus.publish(TOPIC_WIFI, {"ssid": result.wifi_links[0].ssid})
            except Exception:  # noqa: BLE001
                log.exception("failed to store wifi links")

        if result.flows:
            try:
                self._assign_flow_devices(result.flows)
                self.repos.flows.batch_insert(result.flows)
                self.bus.publish(TOPIC_FLOWS, {"count": len(result.flows)})
            except Exception:  # noqa: BLE001
                log.exception("failed to store flows")

        if result.router is not None:
            try:
                self.repos.router.save(result.router)
                self.bus.publish(TOPIC_ROUTER, result.router.fields)
            except Exception:  # noqa: BLE001
                log.exception("failed to store router snapshot")

        if result.health is not None:
            try:
                counts = self.repos.devices.counts()
                self.bus.publish(TOPIC_HEALTH, result.health.to_dict())
            except Exception:  # noqa: BLE001
                log.exception("failed to publish health")

        if result.system:
            for collector in self.registry.collectors:
                if hasattr(collector, "ctx"):
                    collector.ctx.state.update({k: v for k, v in result.system.items() if k in ("dhcp_server", "lease_obtained", "lease_expires", "dhcp_enabled")})
                    collector.ctx.state["system_info"] = result.system
            # feed health state
            for collector in self.registry.collectors:
                if collector.name == "health" and hasattr(collector, "ctx"):
                    collector.ctx.state.setdefault("known_device_counts", self.repos.devices.counts())

    def _assign_flow_devices(self, flows: list) -> None:
        ip_map: dict = {}
        for row in self.repos.devices.list_all():
            if row.get("ipv4"):
                ip_map[row["ipv4"]] = row["device_id"]
            if row.get("ipv6"):
                ip_map[row["ipv6"]] = row["device_id"]
        for f in flows:
            f.device_id = ip_map.get(f.src_ip) or ip_map.get(f.dst_ip)

    def _probe_devices(self) -> None:
        """Rate-limited ICMP probes of currently known devices (bounded concurrency)."""
        if not self.config.get("general.ping_enabled", True):
            return
        devices = self.repos.devices.list_all()
        if not devices:
            return
        counts = self.repos.devices.counts()
        self._set_state("known_device_counts", counts)
        concurrency = int(self.config.get("general.ping_concurrency", 16))
        lock = threading.Lock()
        stop = self._stop

        def probe(row):
            if stop.is_set():
                return
            target = row.get("ipv4")
            if not target:
                return
            res = self.pinger.ping(target, timeout_s=1.0)
            updates = {}
            if res.ok:
                updates["online"] = 1
                updates["probes_sent"] = row.get("probes_sent", 0) + 1
                if res.latency_ms is not None:
                    updates["last_latency_ms"] = res.latency_ms
                    avg = row.get("avg_latency_ms")
                    updates["avg_latency_ms"] = res.latency_ms if avg is None else round(avg * 0.7 + res.latency_ms * 0.3, 2)
                updates["last_seen"] = iso()
                if not row.get("online"):
                    from ..models import NetworkEvent, Session

                    self.repos.events.add(NetworkEvent(event_type="online", device_id=row["device_id"], source="ping"))
                    self.repos.sessions.open_session(
                        Session(device_id=row["device_id"], started_at=iso(), ip=target,
                                connection_type=row.get("connection_type") or "unknown", source="ping")
                    )
                    self.bus.publish("device.online", {"device_id": row["device_id"]})
            else:
                updates["probes_sent"] = row.get("probes_sent", 0) + 1
                updates["probes_lost"] = row.get("probes_lost", 0) + 1
            with lock:
                self.repos.devices.update_fields(row["device_id"], updates)

        sem = threading.Semaphore(concurrency)
        threads = []

        def run(row):
            with sem:
                probe(row)

        for row in devices:
            if stop.is_set():
                break
            if not row.get("ipv4"):
                continue
            t = threading.Thread(target=run, args=(row,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=3.0)

    def _set_state(self, key: str, value) -> None:
        for collector in self.registry.collectors:
            if hasattr(collector, "ctx"):
                collector.ctx.state[key] = value

    def _publish_stats(self) -> None:
        counts = self.repos.devices.counts()
        counts["scan"] = self.scan_count
        self.bus.publish(TOPIC_STATS, counts)

    # ============================================================ interface counters (bandwidth)

    @staticmethod
    def sample_counters() -> dict:
        try:
            import psutil

            io = psutil.net_io_counters()
            return {"ts": time.time(), "bytes_sent": io.bytes_sent, "bytes_recv": io.bytes_recv}
        except Exception:  # noqa: BLE001
            return {}
