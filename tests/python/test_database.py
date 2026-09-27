"""Database layer tests: schema, repositories, retention, failure recovery."""

from __future__ import annotations

from lanwatcher.db import Database, Repositories
from lanwatcher.models import (
    Alert,
    Device,
    FieldRecord,
    FlowObservation,
    InterfaceInfo,
    NetworkEvent,
    RouterSnapshot,
    Session,
    WifiLinkInfo,
)
from lanwatcher.util import epoch_to_iso, iso
import time


def _mk_device(**kw) -> Device:
    base = dict(
        device_id="dev_test0000001",
        mac="aabbccddeeff",
        ipv4="192.168.1.50",
        hostname="laptop-1",
        vendor="Acme Corp",
        connection_type="wifi",
    )
    base.update(kw)
    return Device(**base)


class TestDevices:
    def test_insert_get_find(self, repos):
        d = _mk_device()
        repos.devices.insert(d)
        assert repos.devices.get(d.device_id)["hostname"] == "laptop-1"
        assert repos.devices.find_by_mac("aabbccddeeff")["device_id"] == d.device_id
        assert repos.devices.find_by_ip("192.168.1.50")["device_id"] == d.device_id
        assert repos.devices.find_by_hostname("LAPTOP-1")["device_id"] == d.device_id

    def test_update_and_user_fields(self, repos):
        d = _mk_device()
        repos.devices.insert(d)
        repos.devices.update_fields(d.device_id, {"ipv4": "192.168.1.60", "friendly_name": "Work Laptop", "pinned": 1})
        row = repos.devices.get(d.device_id)
        assert row["ipv4"] == "192.168.1.60"
        assert row["friendly_name"] == "Work Laptop"
        assert row["pinned"] == 1

    def test_update_fields_ignores_unknown_columns(self, repos):
        # column names are schema-whitelisted (no identifier injection)
        d = _mk_device()
        repos.devices.insert(d)
        repos.devices.update_fields(
            d.device_id, {"friendly_name": "ok", "bogus_col); DROP TABLE devices;--": 1}
        )
        row = repos.devices.get(d.device_id)
        assert row["friendly_name"] == "ok"
        assert repos.devices.counts()["total"] == 1

    def test_counts(self, repos):
        repos.devices.insert(_mk_device())
        repos.devices.insert(_mk_device(device_id="dev_x2", mac="112233445566", ipv4="192.168.1.51", connection_type="ethernet"))
        repos.devices.insert(_mk_device(device_id="dev_x3", mac="112233445577", ipv4="192.168.1.52", connection_type="unknown", online=1))
        c = repos.devices.counts()
        assert c["total"] == 3
        assert c["wifi"] == 1 and c["ethernet"] == 1 and c["unknown"] == 1
        assert c["online"] == 1 and c["offline"] == 2

    def test_search(self, repos):
        repos.devices.insert(_mk_device())
        assert repos.devices.search("192.168.1.5")[0]["device_id"] == "dev_test0000001"
        assert repos.devices.search("acme")[0]["device_id"] == "dev_test0000001"
        assert repos.devices.search("no-such-thing") == []

    def test_delete_cascades(self, repos):
        d = _mk_device()
        repos.devices.insert(d)
        repos.events.add(NetworkEvent(event_type="online", device_id=d.device_id))
        repos.sessions.open_session(Session(device_id=d.device_id, started_at=iso()))
        repos.devices.delete(d.device_id)
        assert repos.devices.get(d.device_id) is None
        assert repos.events.list(device_id=d.device_id) == []


class TestObservations:
    def test_record_and_current(self, repos):
        obs_rows = [
            FieldRecord(field="ipv4", value="10.0.0.5", source="arp", observed_at=iso(), confidence="high"),
            FieldRecord(field="hostname", value="pc1", source="dns", observed_at=iso(), confidence="medium"),
        ]
        repos.observations.record("dev_a", obs_rows)
        cur = {r["field"]: r for r in repos.observations.current("dev_a")}
        assert cur["ipv4"]["value"] == "10.0.0.5"
        assert cur["ipv4"]["source"] == "arp"
        assert cur["ipv4"]["confidence"] == "high"

    def test_field_history_and_conflicts(self, repos):
        t1 = epoch_to_iso(time.time() - 60)
        t2 = epoch_to_iso(time.time())
        repos.observations.record("dev_a", [FieldRecord(field="ipv4", value="10.0.0.5", source="arp", observed_at=t1)])
        repos.observations.record("dev_a", [FieldRecord(field="ipv4", value="10.0.0.9", source="neighbor", observed_at=t2)])
        hist = repos.observations.field_history("dev_a", "ipv4")
        assert len(hist) == 2
        conflicts = repos.observations.conflicts("dev_a")
        assert "ipv4" in conflicts
        assert {c["value"] for c in conflicts["ipv4"]} == {"10.0.0.5", "10.0.0.9"}


class TestEventsSessionsAlerts:
    def test_event_timeline(self, repos):
        repos.events.add(NetworkEvent(event_type="ip_change", device_id="dev_a", details={"old": "1.1.1.1", "new": "2.2.2.2"}))
        rows = repos.events.timeline("dev_a")
        assert rows[0]["event_type"] == "ip_change"
        assert "1.1.1.1" in rows[0]["label"]

    def test_event_filters(self, repos):
        for i in range(5):
            repos.events.add(NetworkEvent(event_type="online" if i % 2 else "offline", device_id="dev_a"))
        assert len(repos.events.list(event_type="online")) == 2
        assert len(repos.events.list(limit=3)) == 3

    def test_sessions(self, repos):
        sid = repos.sessions.open_session(Session(device_id="dev_a", started_at=iso(), ip="10.0.0.5"))
        assert sid > 0
        assert repos.sessions.open_for("dev_a") is not None
        assert repos.sessions.close_open("dev_a") == 1
        assert repos.sessions.open_for("dev_a") is None
        assert len(repos.sessions.list_for("dev_a")) == 1

    def test_alerts_triage(self, repos):
        aid = repos.alerts.add(Alert(kind="new_device", severity="warning", message="New device", device_id="dev_a"))
        repos.alerts.set_state(aid, "trusted")
        rows = repos.alerts.list(state="trusted")
        assert rows[0]["id"] == aid
        assert repos.alerts.counts_by_state()["trusted"] == 1
        repos.alerts.resolve(aid)
        assert repos.alerts.list()[0]["resolved_at"] is not None


class TestTelemetry:
    def test_flows_batch(self, repos):
        flows = [
            FlowObservation(src_ip="10.0.0.5", dst_ip="8.8.8.8", src_port=50000, dst_port=443, protocol="tcp", device_id="dev_a"),
            FlowObservation(src_ip="10.0.0.5", dst_ip="1.1.1.1", src_port=50001, dst_port=53, protocol="udp", device_id="dev_a", bytes=120),
        ]
        assert repos.flows.batch_insert(flows) == 2
        s = repos.flows.summary_for_device("dev_a")
        assert s["flows"] == 2
        assert s["bytes_total"] == 120
        assert s["top_destinations"][0]["dst_ip"] in ("8.8.8.8", "1.1.1.1")

    def test_router_snapshot(self, repos):
        snap = RouterSnapshot(source="upnp")
        snap.set("gateway_ip", "192.168.1.1")
        snap.set("wan_status", "Connected")
        snap.set("dhcp_leases", [{"ip": "192.168.1.10", "mac": "aabbccddeeff", "hostname": "phone"}])
        snap.set("dns_servers", ["1.1.1.1", "8.8.8.8"])
        repos.router.save(snap)
        row = repos.router.latest()
        assert row["gateway_ip"] == "192.168.1.1"
        assert row["dhcp_leases"][0]["hostname"] == "phone"
        assert row["dns_servers"] == "1.1.1.1,8.8.8.8"

    def test_interfaces_and_wifi(self, repos):
        repos.interfaces.save_batch(
            [InterfaceInfo(name="Wi-Fi", kind="wifi", is_up=1, ipv4="192.168.1.5", mac="aabbccddeeff")]
        )
        ifaces = repos.interfaces.latest()
        assert ifaces[0]["kind"] == "wifi"
        repos.wifi.save([WifiLinkInfo(interface="Wi-Fi", ssid="HomeNet", signal_pct=80, channel=6, security="WPA2-Personal")])
        w = repos.wifi.latest()
        assert w[0]["ssid"] == "HomeNet"

    def test_tags(self, repos):
        repos.tags.add("dev_a", "office")
        repos.tags.add("dev_a", "office")
        assert repos.tags.for_device("dev_a") == ["office"]
        repos.tags.remove("dev_a", "office")
        assert repos.tags.for_device("dev_a") == []


class TestRetention:
    def test_purge_old(self, repos, db):
        old = epoch_to_iso(time.time() - 100 * 86400)
        repos.events.add(NetworkEvent(event_type="online", device_id="dev_a", ts=old))
        repos.events.add(NetworkEvent(event_type="online", device_id="dev_a"))
        repos.flows.batch_insert([FlowObservation(src_ip=None, dst_ip=None, src_port=None, dst_port=None, protocol="tcp", ts=old)])
        repos.flows.batch_insert([FlowObservation(src_ip=None, dst_ip=None, src_port=None, dst_port=None, protocol="tcp")])
        deleted = repos.retention.purge(flows_days=7, events_days=90, sessions_days=180, alerts_days=180)
        assert deleted["network_events"] == 1
        assert deleted["traffic_flows"] == 1
        assert len(repos.events.list()) == 1

    def test_clear_logs_keeps_devices(self, repos):
        repos.devices.insert(_mk_device())
        repos.events.add(NetworkEvent(event_type="online", device_id="dev_test0000001"))
        repos.retention.clear_logs()
        assert repos.events.list() == []
        assert repos.devices.get("dev_test0000001") is not None

    def test_delete_history(self, repos):
        repos.devices.insert(_mk_device())
        repos.events.add(NetworkEvent(event_type="online", device_id="dev_test0000001"))
        repos.retention.delete_history(keep_devices=True)
        assert repos.events.list() == []
        assert repos.devices.get("dev_test0000001") is not None
        repos.retention.delete_history(keep_devices=False)
        assert repos.devices.get("dev_test0000001") is None


class TestRobustness:
    def test_integrity(self, db):
        assert db.integrity_ok()

    def test_bad_sql_does_not_corrupt(self, repos):
        repos.devices.insert(_mk_device())
        try:
            repos.db.execute("UPDATE devices SET no_such_col=1")
        except Exception:
            pass
        assert repos.devices.get("dev_test0000001") is not None

    def test_reopen_persists(self, tmp_path):
        path = tmp_path / "p.db"
        db1 = Database(path)
        Repositories(db1).devices.insert(_mk_device())
        db1.close()
        db2 = Database(path)
        assert Repositories(db2).devices.find_by_mac("aabbccddeeff") is not None
        db2.close()
