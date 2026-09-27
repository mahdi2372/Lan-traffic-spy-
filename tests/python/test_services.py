"""Service-layer tests: correlation, alerts, discovery, traffic, history, reports, PDF."""

from __future__ import annotations

import time

from lanwatcher.collectors.base import CollectorContext, CollectorRegistry, CollectorResult
from lanwatcher.collectors.simulated import SimulatedCollector, SimulatedNetworkProvider
from lanwatcher.models import DeviceObservation, HealthComponent, HealthSnapshot
from lanwatcher.services.alerts import AlertService
from lanwatcher.services.correlation import CorrelationEngine
from lanwatcher.services.discovery import DiscoveryService
from lanwatcher.services.history import HistoryService, resolve_range
from lanwatcher.services.pdf import PdfDocument, render_simple_pdf
from lanwatcher.services.ping import PingResult, Pinger
from lanwatcher.services.reports import ReportService
from lanwatcher.services.traffic import TrafficService
from lanwatcher.util import CommandRunner, epoch_to_iso, iso


def obs(source, **fields) -> DeviceObservation:
    o = DeviceObservation(source=source)
    for k, v in fields.items():
        o.set(k, v, "high")
    return o


class TestCorrelation:
    def test_same_mac_merges(self, repos):
        eng = CorrelationEngine(repos)
        o1 = obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5", online=True)
        r1 = eng.ingest(o1)
        assert r1.created
        o2 = obs("neighbor", mac="aa:bb:cc:dd:ee:ff", ipv4="192.168.1.5", hostname="phone")
        r2 = eng.ingest(o2)
        assert not r2.created
        assert r2.device_id == r1.device_id
        row = repos.devices.get(r1.device_id)
        assert row["mac_norm"] == "aabbccddeeff"
        assert row["hostname"] == "phone"
        assert row["ipv4"] == "192.168.1.5"

    def test_same_ip_different_mac_does_not_merge(self, repos):
        eng = CorrelationEngine(repos)
        r1 = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5"))
        r2 = eng.ingest(obs("arp", mac="11:22:33:44:55:66", ipv4="192.168.1.5"))
        assert r2.created
        assert r2.device_id != r1.device_id

    def test_ip_change_event(self, repos):
        eng = CorrelationEngine(repos)
        r1 = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5"))
        r2 = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.7"))
        assert r2.device_id == r1.device_id
        kinds = [e.event_type for e in r2.events]
        assert "ip_change" in kinds
        ev = [e for e in r2.events if e.event_type == "ip_change"][0]
        assert ev.details["old"] == "192.168.1.5" and ev.details["new"] == "192.168.1.7"

    def test_hostname_merge_without_mac(self, repos):
        eng = CorrelationEngine(repos)
        r1 = eng.ingest(obs("dns", hostname="laptop-1", ipv4="192.168.1.9"))
        r2 = eng.ingest(obs("dns", hostname="laptop-1", ipv4="192.168.1.9"))
        assert r2.device_id == r1.device_id

    def test_conflicts_kept_not_overwritten(self, repos):
        eng = CorrelationEngine(repos)
        r1 = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", hostname="old-name"))
        o = obs("router_api", mac="AA:BB:CC:DD:EE:FF", hostname="new-name")
        o.set("hostname", "new-name", "low")  # lower confidence must not overwrite
        r2 = eng.ingest(o)
        row = repos.devices.get(r1.device_id)
        assert row["hostname"] == "old-name"
        hist = repos.observations.field_history(r1.device_id, "hostname")
        assert {h["value"] for h in hist} == {"old-name", "new-name"}

    def test_provenance_rows(self, repos):
        eng = CorrelationEngine(repos)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5"))
        cur = {x["field"]: x for x in repos.observations.current(r.device_id)}
        # Field -> Value -> Source -> Timestamp -> Confidence
        assert cur["ipv4"]["value"] == "192.168.1.5"
        assert cur["ipv4"]["source"] == "arp"
        assert cur["ipv4"]["observed_at"].endswith("Z")
        assert cur["ipv4"]["confidence"] == "high"

    def test_offline_detection_and_sessions(self, repos, config):
        config.set("general.offline_threshold_s", 15)
        eng = CorrelationEngine(repos, config=config)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5", online=True))
        assert repos.sessions.open_for(r.device_id) is not None
        # simulate time passing: rewrite last_seen to the past
        repos.devices.update_fields(r.device_id, {"last_seen": epoch_to_iso(time.time() - 3600)})
        events = eng.mark_stale_offline()
        assert any(e.event_type == "offline" and e.device_id == r.device_id for e in events)
        assert repos.sessions.open_for(r.device_id) is None
        assert repos.devices.get(r.device_id)["online"] == 0

    def test_new_device_event(self, repos):
        eng = CorrelationEngine(repos)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF"))
        eng.flush_events(r)
        rows = repos.events.list(device_id=r.device_id)
        assert any(e["event_type"] == "new_device" for e in rows)

    def test_connection_type_unknown_default(self, repos):
        eng = CorrelationEngine(repos)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5"))
        row = repos.devices.get(r.device_id)
        assert row["connection_type"] == "unknown"  # never invent wifi/ethernet


class TestAlerts:
    def test_new_device_alert_flow(self, repos, config, bus):
        svc = AlertService(repos, bus, config)
        eng = CorrelationEngine(repos, bus, config)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.50"))
        alert = svc.on_new_device(r.device_id)
        assert alert is not None
        assert alert.kind == "new_device"
        assert alert.details["ip"] == "192.168.1.50"
        assert alert.details["mac"] == "aa:bb:cc:dd:ee:ff"
        assert alert.details["first_seen"]
        # second call does not duplicate
        assert svc.on_new_device(r.device_id) is None

    def test_triage_states(self, repos, config, bus):
        svc = AlertService(repos, bus, config)
        eng = CorrelationEngine(repos, bus, config)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.50"))
        alert = svc.on_new_device(r.device_id)
        for state in ("trusted", "unknown", "ignore", "investigate"):
            updated = svc.triage(alert.alert_id, state)
            assert updated["state"] == state
        assert svc.triage(alert.alert_id, "bogus") is None
        svc.triage(alert.alert_id, "trusted")
        assert repos.devices.get(r.device_id)["trust_state"] == "trusted"

    def test_ip_change_alert_optional(self, repos, config, bus):
        config.set("alerts.ip_change", True)
        svc = AlertService(repos, bus, config)
        alert = svc.on_device_event("ip_change", "dev_x", {"old": "1.1.1.1", "new": "2.2.2.2"})
        assert alert and alert.kind == "ip_change"
        config.set("alerts.ip_change", False)
        assert svc.on_device_event("ip_change", "dev_y", {"old": "1.1.1.1", "new": "2.2.2.2"}) is None


class TestTraffic:
    def test_rates(self):
        svc = TrafficService()
        s1 = svc.sample()
        time.sleep(0.05)
        s2 = svc.sample()
        assert s1 is not None and s2 is not None
        assert s2.total_bps >= 0
        assert svc.latest() is s2
        assert len(svc.recent(10)) >= 2


class TestHistory:
    def test_range_presets(self):
        since, _ = resolve_range("today")
        assert since.endswith("Z")
        since7, _ = resolve_range("7d")
        assert since7 < since
        assert resolve_range("all")[0] is None
        assert resolve_range("custom", "2026-01-01T00:00:00Z", None)[0] == "2026-01-01T00:00:00Z"

    def test_timeline(self, repos):
        svc = HistoryService(repos)
        eng = CorrelationEngine(repos)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5", online=True))
        eng.flush_events(r)
        rows = svc.timeline(device_id=r.device_id, preset="all")
        assert any(x["event_type"] == "new_device" for x in rows)
        summary = svc.device_summary(r.device_id)
        assert summary["session_count"] == 1
        assert summary["online_seconds"] >= 0


class TestReports:
    def _model(self, repos):
        eng = CorrelationEngine(repos)
        r = eng.ingest(obs("arp", mac="AA:BB:CC:DD:EE:FF", ipv4="192.168.1.5", hostname="pc1", online=True))
        eng.flush_events(r)
        repos.alerts.add(
            __import__("lanwatcher.models", fromlist=["Alert"]).Alert(
                kind="new_device", severity="warning", message="New device pc1", device_id=r.device_id
            )
        )
        return ReportService(repos), r

    def test_json(self, repos):
        svc, _ = self._model(repos)
        import json

        data = json.loads(svc.to_json(svc.build_model("7d")))
        assert data["summary"]["total"] == 1
        assert data["devices"][0]["hostname"] == "pc1"

    def test_csv(self, repos):
        svc, _ = self._model(repos)
        text = svc.to_csv(svc.build_model("7d"))
        assert "pc1" in text and "Summary" in text and "Alerts" in text

    def test_html(self, repos):
        svc, _ = self._model(repos)
        text = svc.to_html(svc.build_model("7d"))
        assert "<html" in text and "Device Inventory" in text and "pc1" in text
        assert "Network Summary" in text and "Traffic Statistics" in text

    def test_pdf(self, repos):
        svc, _ = self._model(repos)
        pdf = svc.to_pdf(svc.build_model("7d"))
        assert pdf.startswith(b"%PDF-1.")
        assert b"%%EOF" in pdf
        assert b"Device Inventory" in pdf

    def test_export_devices_csv(self, repos):
        svc, r = self._model(repos)
        text = svc.export_devices_csv([r.device_id])
        assert "pc1" in text.splitlines()[1]

    def test_generate_dispatch(self, repos):
        svc, _ = self._model(repos)
        for fmt in ("html", "json", "csv", "pdf"):
            content, mime, name = svc.generate(fmt, "7d")
            assert content
            assert name.endswith(fmt if fmt != "html" else "html")
        try:
            svc.generate("xlsx")
            assert False
        except ValueError:
            pass


class TestPdf:
    def test_multipage(self):
        doc = PdfDocument("T")
        for i in range(200):
            doc.paragraph(f"Line {i}")
        data = doc.render()
        assert data.startswith(b"%PDF-1.4")
        import re

        m = re.search(rb"/Count (\d+)", data)
        assert m and int(m.group(1)) >= 3

    def test_escaping(self):
        doc = PdfDocument("T")
        doc.paragraph("fn(x) [100%] \\slash")
        assert b"\\(" in doc.render()


class TestDiscoveryIntegration:
    def test_simulated_discovery_creates_inventory(self, repos, config, bus, tmp_path):
        config.set("general.ping_enabled", False)
        ctx = CollectorContext(config=config, runner=CommandRunner(), is_windows=False, state={"sim_t": 0.0})
        collector = SimulatedCollector(ctx, provider=SimulatedNetworkProvider())
        registry = CollectorRegistry([collector], bus=bus)
        svc = DiscoveryService(config, repos, registry, bus, pinger=Pinger(runner=lambda t, s: PingResult(t, True, 2.0)))

        svc.scan_once()
        counts = repos.devices.counts()
        assert counts["total"] >= 12
        assert counts["online"] >= 8
        assert counts["wifi"] >= 5 and counts["ethernet"] >= 2
        assert counts["unknown"] >= 1  # router without wifi/ethernet evidence

        router = repos.router.latest()
        assert router["gateway_ip"] == "192.168.1.1"
        assert router["dhcp_leases"]

        # advance simulated time past the IP change and rescan
        ctx.state["sim_t"] = 1000.0
        ctx.state["stop_rapid"] = True
        collector.interval_s = 0  # force re-run
        svc.registry.collectors[0].interval_s = 0
        svc._last_run.clear()
        svc.scan_once()

        ip_changes = [e for d in repos.devices.list_all() for e in repos.events.list(device_id=d["device_id"], event_type="ip_change")]
        assert ip_changes, "expected an ip_change event from the simulated scenario"

    def test_failure_recovery(self, repos, config, bus):
        class Flaky:
            name = "flaky"
            description = "flaky"
            interval_s = 0.0
            requires_elevation = False

            def __init__(self, ctx):
                self.ctx = ctx
                self.calls = 0

            def available(self):
                return True, None

            def collect(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient failure")
                return CollectorResult(collector="flaky")

        flaky = Flaky(None)
        registry = CollectorRegistry([flaky], bus=bus)
        svc = DiscoveryService(config, repos, registry, bus, pinger=Pinger(runner=lambda t, s: PingResult(t, True, 1.0)))
        svc.scan_once()
        assert registry.status["flaky"].errors == 1
        svc.scan_once()
        assert registry.status["flaky"].runs == 2
        assert registry.status["flaky"].ok is True  # recovered
