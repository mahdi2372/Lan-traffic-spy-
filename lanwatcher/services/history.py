"""History service: timeline assembly and range filtering (today/24h/7d/30d/custom)."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..db import Repositories
from ..util import epoch_to_iso, iso, parse_iso, utc_now

RANGE_PRESETS = ("today", "24h", "7d", "30d", "all")


def resolve_range(preset: str = "24h", custom_from: Optional[str] = None, custom_to: Optional[str] = None) -> tuple:
    """Return (since_iso | None, until_iso | None)."""
    now = utc_now()
    until = custom_to or None
    if preset == "today":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return iso(midnight), until
    if preset == "24h":
        return iso(now - timedelta(hours=24)), until
    if preset == "7d":
        return iso(now - timedelta(days=7)), until
    if preset == "30d":
        return iso(now - timedelta(days=30)), until
    if preset == "custom":
        return (custom_from or None), (custom_to or None)
    return None, until  # all


class HistoryService:
    def __init__(self, repos: Repositories):
        self.repos = repos

    def timeline(
        self,
        device_id: Optional[str] = None,
        preset: str = "24h",
        custom_from: Optional[str] = None,
        custom_to: Optional[str] = None,
        limit: int = 500,
    ) -> list:
        """Device -> Event -> Timestamp -> Details rows, newest first."""
        since, until = resolve_range(preset, custom_from, custom_to)
        rows = self.repos.events.list(since=since, until=until, device_id=device_id, limit=limit)
        sessions = []
        if device_id:
            for s in self.repos.sessions.list_for(device_id, since=since, limit=50):
                end = s.get("ended_at") or "now"
                rows.append(
                    {
                        "ts": s["started_at"],
                        "event_type": "session",
                        "device_id": device_id,
                        "details": {"ended_at": s.get("ended_at"), "ip": s.get("ip"), "connection_type": s.get("connection_type")},
                        "source": s.get("source"),
                        "label": f"Online session ({s.get('ip') or '?'}) until {end}",
                    }
                )
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows[:limit]

    def device_summary(self, device_id: str) -> dict:
        device = self.repos.devices.get(device_id) or {}
        sessions = self.repos.sessions.list_for(device_id, limit=1000)
        ip_changes = [
            e for e in self.repos.events.list(device_id=device_id, event_type="ip_change", limit=1000)
        ]
        mac_changes = self.repos.events.list(device_id=device_id, event_type="mac_change", limit=100)
        hostname_changes = self.repos.events.list(device_id=device_id, event_type="hostname_change", limit=100)
        conn_changes = self.repos.events.list(device_id=device_id, event_type="connectivity_change", limit=100)
        flow_summary = self.repos.flows.summary_for_device(device_id)
        total_online = 0.0
        for s in sessions:
            start = parse_iso(s["started_at"])
            end = parse_iso(s["ended_at"]) if s.get("ended_at") else utc_now()
            if start and end:
                total_online += max(0.0, (end - start).total_seconds())
        return {
            "device": device,
            "first_seen": device.get("first_seen"),
            "last_seen": device.get("last_seen"),
            "sessions": sessions,
            "session_count": len(sessions),
            "online_seconds": total_online,
            "ip_changes": ip_changes,
            "mac_changes": mac_changes,
            "hostname_changes": hostname_changes,
            "connectivity_changes": conn_changes,
            "traffic": flow_summary,
            "vendor": device.get("vendor"),
            "bandwidth_stats": {
                "bytes_sent": device.get("bytes_sent"),
                "bytes_recv": device.get("bytes_recv"),
            },
        }
