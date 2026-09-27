"""HTTP API + SSE + static serving tests over a real loopback socket."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from lanwatcher.app import create_runtime


@pytest.fixture()
def rt(tmp_path):
    from lanwatcher.services.ping import PingResult, Pinger

    runtime = create_runtime(home=tmp_path / "home", demo=True)
    # speed: no real probes in unit tests (sandbox may block external DNS/HTTP)
    runtime.config.update(
        {
            "privacy.local_only_mode": True,
            "general.ping_enabled": False,
            "general.scan_interval_s": 5,
        }
    )
    fast = Pinger(runner=lambda t, s: PingResult(t, True, latency_ms=1.0))
    runtime.pinger = fast
    runtime.discovery.pinger = fast
    for c in runtime.registry.collectors:
        if hasattr(c, "pinger"):
            c.pinger = fast
    runtime.start()
    runtime.discovery.scan_once()  # synchronous first scan so data exists
    yield runtime
    runtime.stop()


def get(rt, path, token=True, origin=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + rt.token
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(rt.server.origin + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers


def mutate(rt, method, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        rt.server.origin + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + rt.token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestAuth:
    def test_requires_token(self, rt):
        status, data, _ = get(rt, "/api/summary", token=False)
        assert status == 401
        assert data["error"]["what"]

    def test_wrong_token(self, rt):
        req = urllib.request.Request(rt.server.origin + "/api/summary", headers={"Authorization": "Bearer nope"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 401

    def test_cross_origin_rejected(self, rt):
        status, data, _ = get(rt, "/api/summary", origin="https://evil.example")
        assert status == 403

    def test_binds_loopback_only(self, rt):
        assert rt.server.host == "127.0.0.1"


class TestStatic:
    def test_index_served(self, rt):
        req = urllib.request.Request(rt.server.origin + "/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
        assert "LAN Watcher" in body
        assert "Content-Security-Policy" in {k for k in resp.headers.keys()}

    def test_js_modules_served(self, rt):
        for path in ("/js/app.js", "/js/state.js", "/css/app.css", "/js/pages/dashboard.js"):
            req = urllib.request.Request(rt.server.origin + path)
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 200

    def test_traversal_rejected(self, rt):
        req = urllib.request.Request(rt.server.origin + "/%2e%2e/secret.key")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                status = resp.status
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            status = e.code
        # never leak files outside web root: either blocked or SPA fallback
        assert status in (200, 403, 404)
        assert "secret.key" not in body and "api.token" not in body


class TestApi:
    def test_summary(self, rt):
        status, data, _ = get(rt, "/api/summary")
        assert status == 200
        assert data["counts"]["total"] >= 12
        assert "bandwidth" in data and "health" in data and "alerts" in data

    def test_devices_filters_and_search(self, rt):
        _, data, _ = get(rt, "/api/devices?type=wifi")
        assert all(d["connection_type"] == "wifi" for d in data["devices"])
        _, data, _ = get(rt, "/api/devices?status=online")
        assert all(d["online"] for d in data["devices"])
        _, data, _ = get(rt, "/api/search?q=192.168.1.15")
        assert "results" in data

    def test_device_detail_has_provenance(self, rt):
        _, data, _ = get(rt, "/api/devices")
        did = data["devices"][0]["device_id"]
        _, detail, _ = get(rt, f"/api/devices/{did}")
        d = detail["device"]
        assert "observations" in d and "conflicts" in d and "sessions" in d
        for o in d["observations"]:
            assert {"field", "value", "source", "observed_at", "confidence"} <= set(o)

    def test_patch_and_delete_device(self, rt):
        _, data, _ = get(rt, "/api/devices")
        did = data["devices"][-1]["device_id"]
        status, updated = mutate(rt, "PATCH", f"/api/devices/{did}", {"friendly_name": "Lab Pi", "trust_state": "trusted"})
        assert status == 200
        assert updated["device"]["friendly_name"] == "Lab Pi"
        assert updated["device"]["trust_state"] == "trusted"
        status, updated = mutate(rt, "PATCH", f"/api/devices/{did}", {"trust_state": "rootkit"})
        assert status == 400
        assert "why" in updated["error"]
        status, _ = mutate(rt, "DELETE", f"/api/devices/{did}")
        assert status == 200
        status, _, _ = get(rt, f"/api/devices/{did}")
        assert status == 404

    def test_alerts_triage(self, rt):
        _, data, _ = get(rt, "/api/alerts")
        assert data["alerts"]
        aid = data["alerts"][0]["id"]
        status, res = mutate(rt, "POST", f"/api/alerts/{aid}/triage", {"state": "investigate"})
        assert status == 200
        assert res["alert"]["state"] == "investigate"
        status, res = mutate(rt, "POST", f"/api/alerts/{aid}/triage", {"state": "smash"})
        assert status == 400

    def test_router_interfaces_wifi_traffic_health(self, rt):
        for path in ("/api/router", "/api/interfaces", "/api/wifi", "/api/traffic?n=10", "/api/health", "/api/topology", "/api/history?preset=7d"):
            status, data, _ = get(rt, path)
            assert status == 200, path
        _, topo, _ = get(rt, "/api/topology")
        kinds = {n["kind"] for n in topo["nodes"]}
        assert "internet" in kinds and "router" in kinds

    def test_report_download(self, rt):
        for fmt in ("html", "json", "csv", "pdf"):
            req = urllib.request.Request(
                rt.server.origin + f"/api/report/file?format={fmt}&preset=7d",
                headers={"Authorization": "Bearer " + rt.token},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                assert resp.status == 200
                assert "attachment" in (resp.headers.get("Content-Disposition") or "")
            if fmt == "pdf":
                assert body.startswith(b"%PDF-")
            if fmt == "html":
                assert b"Device Inventory" in body

    def test_tools(self, rt):
        status, res = mutate(rt, "POST", "/api/tools/clear-logs")
        assert status == 200
        status, res = mutate(rt, "POST", "/api/tools/purge-retention")
        assert status == 200
        status, res = mutate(rt, "POST", "/api/tools/bogus")
        assert status == 400

    def test_tags_roundtrip(self, rt):
        _, data, _ = get(rt, "/api/devices")
        did = data["devices"][0]["device_id"]
        status, _ = mutate(rt, "POST", f"/api/devices/{did}/tags", {"tag": "living-room"})
        assert status == 201
        status, _ = mutate(rt, "POST", f"/api/devices/{did}/tags", {"tag": "iot"})
        assert status == 201
        _, detail, _ = get(rt, f"/api/devices/{did}")
        assert detail["device"]["tags"] == ["iot", "living-room"]
        status, _ = mutate(rt, "DELETE", f"/api/devices/{did}/tags/iot")
        assert status == 200
        _, detail, _ = get(rt, f"/api/devices/{did}")
        assert detail["device"]["tags"] == ["living-room"]

    def test_settings_roundtrip(self, rt):
        _, before, _ = get(rt, "/api/settings")
        status, res = mutate(rt, "PATCH", "/api/settings", {"values": {"general.scan_interval_s": 20}})
        assert status == 200
        assert res["settings"]["general"]["scan_interval_s"] == 20
        status, res = mutate(rt, "PATCH", "/api/settings", {"values": {"router.password": "hunter2"}})
        assert status == 200
        # password must never round-trip in plaintext or as a secret blob
        _, after, _ = get(rt, "/api/settings")
        blob = json.dumps(after)
        assert "hunter2" not in blob
        assert after["settings"]["router"]["password_ref"] == ""
        assert after["settings"]["router"]["token_ref"] == ""
        assert after["settings"]["router"]["has_password"] is True

    def test_system_diagnostics(self, rt):
        _, data, _ = get(rt, "/api/system")
        assert data["collectors"]
        names = {c["name"] for c in data["collectors"]}
        assert "simulated" in names
        assert "db_size_mb" in data

    def test_error_shape_what_why_fix(self, rt):
        status, data, _ = get(rt, "/api/nope")
        assert status == 404
        err = data["error"]
        assert err["what"] and err["why"] and err["fix"]


class TestSse:
    def test_stream_receives_events(self, rt):
        import threading
        import time

        events = []
        done = threading.Event()

        def reader():
            req = urllib.request.Request(rt.server.origin + "/api/events?token=" + rt.token)
            with urllib.request.urlopen(req, timeout=15) as resp:
                for _ in range(40):
                    line = resp.readline().decode()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                        if len(events) >= 2:
                            done.set()
                    if done.is_set():
                        break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.3)
        mutate(rt, "POST", "/api/scan", {})
        done.wait(12)
        assert events, "expected SSE events"
        topics = {e["topic"] for e in events}
        assert any(t.startswith(("scan.", "device.", "stats", "collector.", "health")) for t in topics)
