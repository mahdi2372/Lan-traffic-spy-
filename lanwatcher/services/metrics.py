"""Process/resource self-monitoring (CPU, RAM, disk for the database)."""

from __future__ import annotations

import os
import sys
import time


def process_metrics() -> dict:
    out = {"cpu_percent": None, "rss_mb": None, "threads": None, "uptime_s": None, "db_size_mb": None}
    try:
        import psutil

        p = psutil.Process(os.getpid())
        out["cpu_percent"] = p.cpu_percent(interval=0.0)
        out["rss_mb"] = round(p.memory_info().rss / (1024 * 1024), 1)
        out["threads"] = p.num_threads()
        out["uptime_s"] = round(time.time() - p.create_time(), 1)
    except Exception:  # noqa: BLE001
        pass
    return out


def db_size(path) -> float:
    try:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = str(path) + suffix
            if os.path.exists(p):
                total += os.path.getsize(p)
        return round(total / (1024 * 1024), 2)
    except OSError:
        return 0.0
