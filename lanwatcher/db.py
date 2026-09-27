"""SQLite storage layer: schema + migrations, repositories, batching, retention.

Single file per process connection set in WAL mode. Writers serialize through a
lock (SQLite handles the rest). All timestamps are ISO-8601 UTC strings so
range queries are plain lexicographic comparisons.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    Alert,
    Device,
    DeviceObservation,
    FieldRecord,
    FlowObservation,
    InterfaceInfo,
    NetworkEvent,
    RouterSnapshot,
    Session,
    WifiLinkInfo,
)
from .util import iso

log = logging.getLogger("lanwatcher.db")

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    mac TEXT,
    mac_norm TEXT,
    ipv4 TEXT,
    ipv6 TEXT,
    hostname TEXT,
    vendor TEXT,
    connection_type TEXT NOT NULL DEFAULT 'unknown',
    interface_name TEXT,
    category TEXT NOT NULL DEFAULT 'unknown',
    friendly_name TEXT,
    notes TEXT,
    notes_encrypted INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    online INTEGER NOT NULL DEFAULT 0,
    last_latency_ms REAL,
    avg_latency_ms REAL,
    probes_sent INTEGER NOT NULL DEFAULT 0,
    probes_lost INTEGER NOT NULL DEFAULT 0,
    bytes_sent INTEGER,
    bytes_recv INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    trust_state TEXT NOT NULL DEFAULT 'unknown',
    confidence TEXT NOT NULL DEFAULT 'low',
    is_self INTEGER NOT NULL DEFAULT 0,
    source_summary TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS field_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'medium',
    is_current INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS device_interfaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    interface_name TEXT,
    mac TEXT,
    ip TEXT,
    connection_type TEXT NOT NULL DEFAULT 'unknown',
    link_speed_bps INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    ip TEXT,
    connection_type TEXT NOT NULL DEFAULT 'unknown',
    source TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS network_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    device_id TEXT,
    event_type TEXT NOT NULL,
    details TEXT,
    source TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS traffic_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    device_id TEXT,
    src_ip TEXT,
    dst_ip TEXT,
    src_port INTEGER,
    dst_port INTEGER,
    protocol TEXT NOT NULL,
    bytes INTEGER,
    duration_s REAL,
    state TEXT,
    source TEXT NOT NULL DEFAULT 'flow'
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    device_id TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    details TEXT,
    state TEXT NOT NULL DEFAULT 'unknown',
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS router_information (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    router_ip TEXT,
    gateway_ip TEXT,
    hostname TEXT,
    lan_subnet TEXT,
    dhcp_range_start TEXT,
    dhcp_range_end TEXT,
    wan_ip TEXT,
    wan_status TEXT,
    wan_uptime_s REAL,
    model TEXT,
    vendor TEXT,
    dns_servers TEXT,
    ipv6_gateway TEXT,
    ipv6_wan TEXT,
    uptime_s REAL,
    connected_clients INTEGER,
    dhcp_leases_json TEXT,
    source TEXT NOT NULL DEFAULT 'router',
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS network_interfaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    mac TEXT,
    kind TEXT NOT NULL DEFAULT 'unknown',
    is_up INTEGER NOT NULL DEFAULT 0,
    link_speed_bps INTEGER,
    mtu INTEGER,
    ipv4 TEXT,
    ipv4_prefix INTEGER,
    ipv6 TEXT,
    gateway TEXT,
    dns TEXT,
    profile TEXT,
    source TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS wifi_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    interface TEXT,
    ssid TEXT,
    bssid TEXT,
    signal_pct INTEGER,
    channel INTEGER,
    radio_type TEXT,
    receive_rate_mbps REAL,
    transmit_rate_mbps REAL,
    frequency_mhz INTEGER,
    band TEXT,
    security TEXT,
    state TEXT,
    source TEXT NOT NULL DEFAULT 'wlan'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    UNIQUE(device_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac_norm);
CREATE INDEX IF NOT EXISTS idx_devices_ip4 ON devices(ipv4);
CREATE INDEX IF NOT EXISTS idx_devices_hostname ON devices(hostname);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen);
CREATE INDEX IF NOT EXISTS idx_fields_device ON field_observations(device_id, field);
CREATE INDEX IF NOT EXISTS idx_events_ts ON network_events(ts);
CREATE INDEX IF NOT EXISTS idx_events_device ON network_events(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_flows_ts ON traffic_flows(ts);
CREATE INDEX IF NOT EXISTS idx_flows_device ON traffic_flows(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id);
CREATE INDEX IF NOT EXISTS idx_sessions_device ON device_sessions(device_id, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON device_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_ifaces_ts ON network_interfaces(captured_at);
"""


class Database:
    """Thread-safe SQLite access. Each thread gets its own connection (WAL)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.migrate()

    # -------------------------------------------------- connections

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def write(self):
        """Serialized write transaction with automatic commit/rollback."""
        with self._write_lock:
            conn = self.conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def query(self, sql: str, params: tuple = ()) -> list:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.write() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid or 0

    def executemany(self, sql: str, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self.write() as conn:
            cur = conn.executemany(sql, rows)
            return cur.rowcount

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -------------------------------------------------- schema

    def migrate(self) -> None:
        with self.write() as conn:
            conn.executescript(SCHEMA_SQL)
            row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            # future migrations: compare int(row['value']) and apply upgrades here

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def integrity_ok(self) -> bool:
        row = self.query_one("PRAGMA integrity_check")
        return bool(row and list(row.values())[0] == "ok")


# ================================================================= devices


class DevicesRepo:
    USER_FIELDS = ("friendly_name", "notes", "notes_encrypted", "category", "pinned", "trust_state")

    def __init__(self, db: Database):
        self.db = db

    def insert(self, d: Device) -> None:
        data = d.to_dict()
        if data.get("mac") and not data.get("mac_norm"):
            from .util import normalize_mac

            data["mac_norm"] = normalize_mac(data["mac"])
        cols = list(data.keys())
        self.db.execute(
            f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(data[c] for c in cols),
        )

    def update_fields(self, device_id: str, updates: dict) -> None:
        if not updates:
            return
        updates = dict(updates)
        if "mac" in updates and "mac_norm" not in updates:
            from .util import normalize_mac

            updates["mac_norm"] = normalize_mac(updates.get("mac"))
        updates["updated_at"] = iso()
        # whitelist column names against the live schema (no interpolation of
        # caller-controlled identifiers)
        valid = {r["name"] for r in self.db.query("PRAGMA table_info(devices)")}
        updates = {k: v for k, v in updates.items() if k in valid}
        sets = ", ".join(f"{k}=?" for k in updates)
        self.db.execute(f"UPDATE devices SET {sets} WHERE device_id=?", tuple(updates.values()) + (device_id,))

    def get(self, device_id: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM devices WHERE device_id=?", (device_id,))

    def find_by_mac(self, mac_norm: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM devices WHERE mac_norm=?", (mac_norm,))

    def find_by_ip(self, ip: str) -> Optional[dict]:
        rows = self.db.query("SELECT * FROM devices WHERE ipv4=? OR ipv6=? ORDER BY last_seen DESC", (ip, ip))
        return rows[0] if rows else None

    def find_by_hostname(self, hostname: str) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM devices WHERE lower(hostname)=lower(?)", (hostname,))

    def find_self(self) -> Optional[dict]:
        return self.db.query_one("SELECT * FROM devices WHERE is_self=1 LIMIT 1")

    def list_all(self, include_history_deleted: bool = False) -> list:
        return self.db.query("SELECT * FROM devices ORDER BY pinned DESC, last_seen DESC")

    def list_online(self) -> list:
        return self.db.query("SELECT * FROM devices WHERE online=1")

    def counts(self) -> dict:
        rows = self.db.query(
            "SELECT connection_type, online, COUNT(*) AS n FROM devices GROUP BY connection_type, online"
        )
        out = {"total": 0, "online": 0, "offline": 0, "wifi": 0, "ethernet": 0, "unknown": 0, "wifi_online": 0, "ethernet_online": 0, "unknown_online": 0}
        for r in rows:
            out["total"] += r["n"]
            key = "online" if r["online"] else "offline"
            out[key] += r["n"]
            ct = r["connection_type"] if r["connection_type"] in ("wifi", "ethernet") else "unknown"
            out[ct] += r["n"]
            if r["online"]:
                out[ct + "_online"] += r["n"]
        return out

    def delete(self, device_id: str) -> None:
        with self.db.write() as conn:
            for table in ("devices", "field_observations", "device_interfaces", "device_sessions", "network_events", "traffic_flows", "alerts", "tags"):
                conn.execute(f"DELETE FROM {table} WHERE device_id=?", (device_id,))

    def search(self, term: str) -> list:
        like = f"%{term}%"
        return self.db.query(
            """SELECT * FROM devices WHERE
               device_id LIKE ? OR mac_norm LIKE ? OR mac LIKE ? OR ipv4 LIKE ? OR ipv6 LIKE ?
               OR hostname LIKE ? OR vendor LIKE ? OR friendly_name LIKE ? OR category LIKE ?
               OR connection_type LIKE ? OR trust_state LIKE ?
               ORDER BY pinned DESC, last_seen DESC""",
            (like,) * 11,
        )


# ================================================================= observations


class ObservationsRepo:
    def __init__(self, db: Database):
        self.db = db

    def record(self, device_id: str, records: list) -> int:
        rows = []
        for r in records:
            if isinstance(r, FieldRecord):
                rows.append((device_id, r.field, r.value, r.source, r.observed_at, r.confidence))
            elif isinstance(r, tuple):
                rows.append(r[:6])
            else:  # ObservedValue.as_row output
                rows.append(tuple(r)[:6])
        return self.db.executemany(
            "INSERT INTO field_observations(device_id, field, value, source, observed_at, confidence, is_current)"
            " VALUES(?, ?, ?, ?, ?, ?, 1)",
            rows,
        )

    def record_observation(self, device_id: str, obs: DeviceObservation) -> int:
        rows = [ov.as_row(device_id, name) for name, ov in obs.fields.items()]
        return self.record(device_id, rows)

    def mark_superseded(self, device_id: str, field: str, keep_id: Optional[int] = None) -> None:
        self.db.execute(
            "UPDATE field_observations SET is_current=0 WHERE device_id=? AND field=? AND is_current=1 AND id<>COALESCE(?, -1)",
            (device_id, field, keep_id),
        )

    def current(self, device_id: str) -> list:
        return self.db.query(
            "SELECT * FROM field_observations WHERE device_id=? AND is_current=1 ORDER BY observed_at DESC",
            (device_id,),
        )

    def field_history(self, device_id: str, field: Optional[str] = None) -> list:
        if field:
            return self.db.query(
                "SELECT * FROM field_observations WHERE device_id=? AND field=? ORDER BY observed_at DESC",
                (device_id, field),
            )
        return self.db.query(
            "SELECT * FROM field_observations WHERE device_id=? ORDER BY observed_at DESC",
            (device_id,),
        )

    def conflicts(self, device_id: str) -> dict:
        """Fields with more than one distinct current/historical value."""
        rows = self.db.query(
            "SELECT field, value, source, observed_at, confidence FROM field_observations"
            " WHERE device_id=? ORDER BY field, observed_at DESC",
            (device_id,),
        )
        by_field: dict = {}
        for r in rows:
            by_field.setdefault(r["field"], []).append(r)
        out = {}
        for f, lst in by_field.items():
            values = {x["value"] for x in lst if x["value"] is not None}
            if len(values) > 1:
                out[f] = lst
        return out


# ================================================================= events / sessions / alerts


class EventsRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, ev: NetworkEvent) -> int:
        return self.db.execute(
            "INSERT INTO network_events(ts, device_id, event_type, details, source) VALUES(?, ?, ?, ?, ?)",
            (ev.ts, ev.device_id, ev.event_type, json.dumps(ev.details or {}), ev.source),
        )

    def list(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        device_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 500,
    ) -> list:
        sql = "SELECT * FROM network_events WHERE 1=1"
        params: list = []
        if since:
            sql += " AND ts>=?"
            params.append(since)
        if until:
            sql += " AND ts<=?"
            params.append(until)
        if device_id:
            sql += " AND device_id=?"
            params.append(device_id)
        if event_type:
            sql += " AND event_type=?"
            params.append(event_type)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        for r in rows:
            r["details"] = json.loads(r["details"]) if r.get("details") else {}
        return rows

    def timeline(self, device_id: str, since: Optional[str] = None, until: Optional[str] = None, limit: int = 1000) -> list:
        """Device -> Event -> Timestamp -> Details rows (also folds sessions)."""
        rows = self.list(since=since, until=until, device_id=device_id, limit=limit)
        for r in rows:
            r["label"] = NetworkEvent(
                event_type=r["event_type"], device_id=r["device_id"], details=r["details"]
            ).describe()
        return rows


class SessionsRepo:
    def __init__(self, db: Database):
        self.db = db

    def open_session(self, s: Session) -> int:
        return self.db.execute(
            "INSERT INTO device_sessions(device_id, started_at, ended_at, ip, connection_type, source)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (s.device_id, s.started_at, s.ended_at, s.ip, s.connection_type, s.source),
        )

    def close_open(self, device_id: str, ended_at: Optional[str] = None) -> int:
        ended = ended_at or iso()
        with self.db.write() as conn:
            cur = conn.execute(
                "UPDATE device_sessions SET ended_at=? WHERE device_id=? AND ended_at IS NULL",
                (ended, device_id),
            )
            return cur.rowcount

    def open_for(self, device_id: str) -> Optional[dict]:
        return self.db.query_one(
            "SELECT * FROM device_sessions WHERE device_id=? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (device_id,),
        )

    def list_for(self, device_id: str, since: Optional[str] = None, limit: int = 200) -> list:
        sql = "SELECT * FROM device_sessions WHERE device_id=?"
        params: list = [device_id]
        if since:
            sql += " AND started_at>=?"
            params.append(since)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, tuple(params))


class AlertsRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, a: Alert) -> int:
        return self.db.execute(
            "INSERT INTO alerts(ts, device_id, kind, severity, message, details, state, resolved_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (a.ts, a.device_id, a.kind, a.severity, a.message, json.dumps(a.details or {}), a.state, a.resolved_at),
        )

    def set_state(self, alert_id: int, state: str) -> None:
        self.db.execute("UPDATE alerts SET state=? WHERE id=?", (state, alert_id))

    def resolve(self, alert_id: int) -> None:
        self.db.execute("UPDATE alerts SET resolved_at=? WHERE id=?", (iso(), alert_id))

    def list(self, state: Optional[str] = None, kind: Optional[str] = None, since: Optional[str] = None, limit: int = 500) -> list:
        sql = "SELECT * FROM alerts WHERE 1=1"
        params: list = []
        if state:
            sql += " AND state=?"
            params.append(state)
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if since:
            sql += " AND ts>=?"
            params.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        for r in rows:
            r["details"] = json.loads(r["details"]) if r.get("details") else {}
        return rows

    def counts_by_state(self) -> dict:
        rows = self.db.query("SELECT state, COUNT(*) AS n FROM alerts GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    def active_new_device_alert_for(self, device_id: str) -> Optional[dict]:
        return self.db.query_one(
            "SELECT * FROM alerts WHERE device_id=? AND kind='new_device' ORDER BY ts DESC LIMIT 1",
            (device_id,),
        )


# ================================================================= telemetry storage


class FlowsRepo:
    def __init__(self, db: Database):
        self.db = db

    def batch_insert(self, flows: list) -> int:
        rows = [
            (f.ts, f.device_id, f.src_ip, f.dst_ip, f.src_port, f.dst_port, f.protocol, f.bytes, f.duration_s, f.state, f.source)
            for f in flows
        ]
        return self.db.executemany(
            "INSERT INTO traffic_flows(ts, device_id, src_ip, dst_ip, src_port, dst_port, protocol, bytes, duration_s, state, source)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def recent(self, limit: int = 200, device_id: Optional[str] = None) -> list:
        if device_id:
            return self.db.query(
                "SELECT * FROM traffic_flows WHERE device_id=? ORDER BY ts DESC LIMIT ?", (device_id, limit)
            )
        return self.db.query("SELECT * FROM traffic_flows ORDER BY ts DESC LIMIT ?", (limit,))

    def summary_for_device(self, device_id: str, since: Optional[str] = None) -> dict:
        sql = (
            "SELECT COUNT(*) AS flows, SUM(COALESCE(bytes,0)) AS bytes_total,"
            " SUM(CASE WHEN dst_port IS NOT NULL THEN 1 ELSE 0 END) AS with_ports"
            " FROM traffic_flows WHERE device_id=?"
        )
        params: list = [device_id]
        if since:
            sql += " AND ts>=?"
            params.append(since)
        row = self.db.query_one(sql, tuple(params)) or {}
        top_dests = self.db.query(
            "SELECT dst_ip, COUNT(*) AS n FROM traffic_flows WHERE device_id=? AND dst_ip IS NOT NULL"
            " GROUP BY dst_ip ORDER BY n DESC LIMIT 10",
            (device_id,),
        )
        top_ports = self.db.query(
            "SELECT dst_port, protocol, COUNT(*) AS n FROM traffic_flows WHERE device_id=? AND dst_port IS NOT NULL"
            " GROUP BY dst_port, protocol ORDER BY n DESC LIMIT 10",
            (device_id,),
        )
        return {"flows": row.get("flows") or 0, "bytes_total": row.get("bytes_total") or 0, "top_destinations": top_dests, "top_ports": top_ports}

    def totals_since(self, since: str) -> dict:
        row = self.db.query_one(
            "SELECT COUNT(*) AS flows, SUM(COALESCE(bytes,0)) AS bytes_total FROM traffic_flows WHERE ts>=?",
            (since,),
        )
        return row or {"flows": 0, "bytes_total": 0}


class RouterRepo:
    FIELDS = (
        "router_ip", "gateway_ip", "hostname", "lan_subnet", "dhcp_range_start", "dhcp_range_end",
        "wan_ip", "wan_status", "wan_uptime_s", "model", "vendor", "dns_servers", "ipv6_gateway",
        "ipv6_wan", "uptime_s", "connected_clients", "dhcp_leases_json",
    )

    def __init__(self, db: Database):
        self.db = db

    def save(self, snap: RouterSnapshot) -> int:
        vals = {k: snap.get(k) for k in self.FIELDS}
        leases = snap.get("dhcp_leases")
        vals["dhcp_leases_json"] = json.dumps(leases) if leases else None
        if isinstance(vals.get("dns_servers"), (list, tuple)):
            vals["dns_servers"] = ",".join(vals["dns_servers"])
        cols = ["captured_at", "source"] + list(self.FIELDS)
        return self.db.execute(
            f"INSERT INTO router_information ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            (snap.ts, snap.source) + tuple(vals[k] for k in self.FIELDS),
        )

    def latest(self) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM router_information ORDER BY captured_at DESC, id DESC LIMIT 1")
        if row and row.get("dhcp_leases_json"):
            row["dhcp_leases"] = json.loads(row["dhcp_leases_json"])
        elif row:
            row["dhcp_leases"] = []
        return row

    def history(self, limit: int = 50) -> list:
        return self.db.query("SELECT captured_at, wan_status, wan_ip, connected_clients, source FROM router_information ORDER BY captured_at DESC LIMIT ?", (limit,))


class InterfacesRepo:
    COLS = ("name", "description", "mac", "kind", "is_up", "link_speed_bps", "mtu", "ipv4", "ipv4_prefix", "ipv6", "gateway", "dns", "profile", "source")

    def __init__(self, db: Database):
        self.db = db

    def save_batch(self, ifaces: list, captured_at: Optional[str] = None) -> None:
        ts = captured_at or iso()
        rows = [tuple([ts] + [getattr(i, c) for c in self.COLS]) for i in ifaces]
        placeholders = ", ".join(["?"] * (len(self.COLS) + 1))
        self.db.executemany(
            f"INSERT INTO network_interfaces (captured_at, {', '.join(self.COLS)}) VALUES ({placeholders})",
            rows,
        )

    def latest(self) -> list:
        row = self.db.query_one("SELECT MAX(captured_at) AS ts FROM network_interfaces")
        if not row or not row["ts"]:
            return []
        return self.db.query(
            "SELECT * FROM network_interfaces WHERE captured_at=? ORDER BY name", (row["ts"],)
        )


class WifiRepo:
    COLS = ("interface", "ssid", "bssid", "signal_pct", "channel", "radio_type", "receive_rate_mbps",
            "transmit_rate_mbps", "frequency_mhz", "band", "security", "state", "source")

    def __init__(self, db: Database):
        self.db = db

    def save(self, links: list, captured_at: Optional[str] = None) -> None:
        ts = captured_at or iso()
        rows = [tuple([ts] + [getattr(w, c) for c in self.COLS]) for w in links]
        placeholders = ", ".join(["?"] * (len(self.COLS) + 1))
        self.db.executemany(
            f"INSERT INTO wifi_links (captured_at, {', '.join(self.COLS)}) VALUES ({placeholders})",
            rows,
        )

    def latest(self) -> list:
        row = self.db.query_one("SELECT MAX(captured_at) AS ts FROM wifi_links")
        if not row or not row["ts"]:
            return []
        return self.db.query("SELECT * FROM wifi_links WHERE captured_at=?", (row["ts"],))


class TagsRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, device_id: str, tag: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO tags(device_id, tag) VALUES(?, ?)", (device_id, tag))

    def remove(self, device_id: str, tag: str) -> None:
        self.db.execute("DELETE FROM tags WHERE device_id=? AND tag=?", (device_id, tag))

    def for_device(self, device_id: str) -> list:
        return [r["tag"] for r in self.db.query("SELECT tag FROM tags WHERE device_id=? ORDER BY tag", (device_id,))]

    def all_tags(self) -> list:
        return self.db.query("SELECT tag, COUNT(*) AS n FROM tags GROUP BY tag ORDER BY tag")


# ================================================================= retention


class RetentionRepo:
    """Keeps historical tables bounded. Safe to run periodically."""

    def __init__(self, db: Database):
        self.db = db

    def purge(self, flows_days: int = 7, events_days: int = 90, sessions_days: int = 180, alerts_days: int = 180, vacuum: bool = False) -> dict:
        from .util import epoch_to_iso, utc_now
        from datetime import timedelta

        now = utc_now()
        cutoffs = {
            "traffic_flows": epoch_to_iso((now - timedelta(days=flows_days)).timestamp()),
            "network_events": epoch_to_iso((now - timedelta(days=events_days)).timestamp()),
            "device_sessions": epoch_to_iso((now - timedelta(days=sessions_days)).timestamp()),
            "alerts": epoch_to_iso((now - timedelta(days=alerts_days)).timestamp()),
            "field_observations": epoch_to_iso((now - timedelta(days=events_days)).timestamp()),
        }
        deleted = {}
        with self.db.write() as conn:
            deleted["traffic_flows"] = conn.execute("DELETE FROM traffic_flows WHERE ts<?", (cutoffs["traffic_flows"],)).rowcount
            deleted["network_events"] = conn.execute("DELETE FROM network_events WHERE ts<?", (cutoffs["network_events"],)).rowcount
            deleted["device_sessions"] = conn.execute(
                "DELETE FROM device_sessions WHERE started_at<? AND (ended_at IS NULL OR ended_at<?)",
                (cutoffs["device_sessions"], cutoffs["device_sessions"]),
            ).rowcount
            deleted["alerts"] = conn.execute("DELETE FROM alerts WHERE ts<?", (cutoffs["alerts"],)).rowcount
            deleted["field_observations"] = conn.execute(
                "DELETE FROM field_observations WHERE is_current=0 AND observed_at<?", (cutoffs["field_observations"],)
            ).rowcount
            # telemetry snapshots: keep the most recent 500 rows
            for table, ts_col in (("router_information", "captured_at"), ("network_interfaces", "captured_at"), ("wifi_links", "captured_at")):
                deleted[table] = conn.execute(
                    f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY {ts_col} DESC, id DESC LIMIT 500)"
                ).rowcount
        if vacuum:
            self.db.execute("VACUUM")
        return deleted

    def clear_logs(self) -> None:
        """Explicit 'clear logs' action requested by the operator."""
        with self.db.write() as conn:
            conn.execute("DELETE FROM network_events")
            conn.execute("DELETE FROM traffic_flows")

    def delete_history(self, keep_devices: bool = True) -> None:
        with self.db.write() as conn:
            conn.execute("DELETE FROM network_events")
            conn.execute("DELETE FROM traffic_flows")
            conn.execute("DELETE FROM device_sessions")
            conn.execute("DELETE FROM field_observations")
            conn.execute("DELETE FROM alerts")
            conn.execute("DELETE FROM router_information")
            conn.execute("DELETE FROM network_interfaces")
            conn.execute("DELETE FROM wifi_links")
            if not keep_devices:
                conn.execute("DELETE FROM devices")
                conn.execute("DELETE FROM tags")


class SettingsRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.db.get_meta(key, default)

    def set(self, key: str, value: str) -> None:
        self.db.set_meta(key, value)


class Repositories:
    """Convenience bundle used by services."""

    def __init__(self, db: Database):
        self.db = db
        self.devices = DevicesRepo(db)
        self.observations = ObservationsRepo(db)
        self.events = EventsRepo(db)
        self.sessions = SessionsRepo(db)
        self.alerts = AlertsRepo(db)
        self.flows = FlowsRepo(db)
        self.router = RouterRepo(db)
        self.interfaces = InterfacesRepo(db)
        self.wifi = WifiRepo(db)
        self.tags = TagsRepo(db)
        self.retention = RetentionRepo(db)
        self.settings = SettingsRepo(db)
