"""Traffic service: bandwidth rates from OS interface counters (metadata only).

Per-interface byte counters are sampled periodically; rates are deltas. Total
bandwidth = sum of active non-loopback interfaces. No packet contents ever.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BandwidthSample:
    ts: float
    up_bps: float = 0.0
    down_bps: float = 0.0
    total_bps: float = 0.0
    bytes_sent_total: int = 0
    bytes_recv_total: int = 0
    per_iface: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "up_bps": round(self.up_bps, 1),
            "down_bps": round(self.down_bps, 1),
            "total_bps": round(self.total_bps, 1),
            "bytes_sent_total": self.bytes_sent_total,
            "bytes_recv_total": self.bytes_recv_total,
        }


class TrafficService:
    def __init__(self, history_len: int = 720):
        self._lock = threading.Lock()
        self._prev: Optional[dict] = None
        self.history: list = []
        self._history_len = history_len
        self.available = True
        self.last_error: Optional[str] = None

    def sample(self) -> Optional[BandwidthSample]:
        try:
            import psutil

            io = psutil.net_io_counters()
            per_iface = psutil.net_io_counters(pernic=True)
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

        now = time.time()
        totals = {"bytes_sent": io.bytes_sent, "bytes_recv": io.bytes_recv, "ts": now}
        iface_deltas = {}
        with self._lock:
            prev = self._prev
            self._prev = {"totals": totals, "per_iface": {k: (v.bytes_sent, v.bytes_recv) for k, v in per_iface.items() if k != "lo"}}
            if prev is None:
                sample = BandwidthSample(ts=now)
                self.history.append(sample)
                return sample

            dt = max(0.001, now - prev["totals"]["ts"])
            up = max(0.0, (io.bytes_sent - prev["totals"]["bytes_sent"]) * 8 / dt)
            down = max(0.0, (io.bytes_recv - prev["totals"]["bytes_recv"]) * 8 / dt)
            for name, cur in per_iface.items():
                if name == "lo":
                    continue
                p = prev["per_iface"].get(name)
                if p:
                    iface_deltas[name] = {
                        "up_bps": max(0.0, (cur.bytes_sent - p[0]) * 8 / dt),
                        "down_bps": max(0.0, (cur.bytes_recv - p[1]) * 8 / dt),
                    }
            sample = BandwidthSample(
                ts=now,
                up_bps=up,
                down_bps=down,
                total_bps=up + down,
                bytes_sent_total=io.bytes_sent,
                bytes_recv_total=io.bytes_recv,
                per_iface=iface_deltas,
            )
            self.history.append(sample)
            if len(self.history) > self._history_len:
                self.history = self.history[-self._history_len :]
            return sample

    def latest(self) -> Optional[BandwidthSample]:
        with self._lock:
            return self.history[-1] if self.history else None

    def recent(self, n: int = 120) -> list:
        with self._lock:
            return [s.to_dict() for s in self.history[-n:]]
