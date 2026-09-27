"""Collector tests: isolation, ARP/neighbour/Windows/Wi-Fi/flow/health/router,
simulated provider, and failure recovery."""

from __future__ import annotations

import io
import json

from tests.python.conftest import fixture_text

from lanwatcher.collectors.arp import ARPCollector
from lanwatcher.collectors.base import (
    Collector,
    CollectorContext,
    CollectorRegistry,
    CollectorResult,
)
from lanwatcher.collectors.flow import FlowCollector
from lanwatcher.collectors.health import HealthCollector
from lanwatcher.collectors.neighbors import NeighborCollector
from lanwatcher.collectors.router import RouterCollector
from lanwatcher.collectors.simulated import SimulatedCollector, SimulatedNetworkProvider
from lanwatcher.collectors.wifi import WiFiCollector
from lanwatcher.collectors.windows_net import WindowsNetworkCollector
from lanwatcher.parsing.oui import OuiLookup
from lanwatcher.services.ping import PingResult, Pinger
from lanwatcher.util import CommandResult, CommandRunner


def make_ctx(config, responses: dict, is_windows=True, state=None):
    """responses: {'arp -a': CommandResult|callable(args)->CommandResult} keyed by first token or full join."""

    def runner(args):
        key = args[0]
        full = " ".join(args)
        val = responses.get(full) or responses.get(key)
        if val is None:
            return CommandResult(False, "", "", -1, error="command not found")
        if callable(val):
            return val(args)
        return val

    return CollectorContext(
        config=config,
        runner=CommandRunner(runner=runner),
        oui=OuiLookup(),
        is_windows=is_windows,
        state=state if state is not None else {},
    )


def ok(text: str) -> CommandResult:
    return CommandResult(True, text, "", 0)


class TestIsolation:
    def test_bad_collector_does_not_crash_others(self, config):
        class Boom(Collector):
            name = "boom"

            def collect(self):
                raise RuntimeError("kaboom")

        class Fine(Collector):
            name = "fine"

            def collect(self):
                return CollectorResult(collector="fine")

        reg = CollectorRegistry([Boom(None), Fine(None)])
        merged = reg.run_all()
        assert reg.status["boom"].ok is False
        assert "kaboom" in (reg.status["boom"].last_error or "")
        assert reg.status["fine"].ok is True
        assert merged.collector == "*"

    def test_unavailable_path(self, config):
        class Unavail(Collector):
            name = "unavail"

            def available(self):
                return False, "needs windows"

            def collect(self):  # pragma: no cover
                raise AssertionError("must not run")

        reg = CollectorRegistry([Unavail(None)])
        r = reg.run_one(reg.collectors[0])
        assert r.ok is False
        assert r.unavailable_reason == "needs windows"


class TestArpCollector:
    def test_windows_arp(self, config):
        ctx = make_ctx(config, {"arp": ok(fixture_text("arp_windows.txt"))})
        r = ARPCollector(ctx).collect()
        assert r.ok
        macs = {o.mac for o in r.observations if o.mac}
        assert "001122334455" in macs
        ips = {o.ipv4 for o in r.observations if o.ipv4}
        assert "192.168.1.1" in ips
        assert not any(o.ipv4 == "192.168.1.255" for o in r.observations)  # broadcast skipped? ff:ff kept out

    def test_missing_command(self, config):
        ctx = make_ctx(config, {})
        r = ARPCollector(ctx).collect()
        assert r.ok is False
        assert r.unavailable_reason


class TestNeighborCollector:
    def test_windows_neighbors(self, config):
        def runner(args):
            if args[:2] == ["netsh", "interface"] and args[2] == "ipv4":
                return ok(fixture_text("netsh_neighbors_v4.txt"))
            if args[:2] == ["netsh", "interface"] and args[2] == "ipv6":
                return ok(fixture_text("netsh_neighbors_v6.txt"))
            return CommandResult(False, "", "", -1)

        ctx = CollectorContext(config=config, runner=CommandRunner(runner=runner), is_windows=True)
        r = NeighborCollector(ctx).collect()
        assert r.ok
        macs = {o.mac for o in r.observations if o.mac}
        assert "001122334455" in macs and "a4bb6d1f8877" in macs
        assert not any(o.ipv6 for o in r.observations if o.ipv6 and o.ipv6.startswith("ff02"))  # multicast filtered


class TestWindowsNetworkCollector:
    def test_full(self, config):
        responses = {
            "ipconfig": ok(fixture_text("ipconfig_all.txt")),
            "route": ok(fixture_text("route_print.txt")),
            "netsh": ok(fixture_text("netsh_interface_show.txt")),
        }

        def runner(args):
            if args[0] == "netsh" and "currentprofile" in args:
                return ok(fixture_text("netsh_firewall.txt"))
            if args[0] == "netsh" and "interfaces" in args and "ipv4" in args:
                return ok(fixture_text("netsh_ip_interfaces.txt"))
            return responses.get(args[0], CommandResult(False, "", "", -1))

        ctx = CollectorContext(config=config, runner=CommandRunner(runner=runner), is_windows=True)
        r = WindowsNetworkCollector(ctx).collect()
        kinds = {i.name: i.kind for i in r.interfaces}
        assert kinds.get("Wi-Fi") == "wifi"
        assert kinds.get("Ethernet") == "ethernet"
        assert r.system["default_gateway"] == "192.168.1.1"
        assert r.system["firewall"]["enabled"] is True
        assert ctx.state.get("gateway_ip") == "192.168.1.1"
        self_obs = [o for o in r.observations if o.raw.get("is_self")]
        assert self_obs and self_obs[0].ipv4 == "192.168.1.50"
        gw_obs = [o for o in r.observations if o.get("category") == "router"]
        assert gw_obs and gw_obs[0].ipv4 == "192.168.1.1"


class TestWifiCollector:
    def test_windows_wifi(self, config):
        def runner(args):
            if "networks" in args:
                return ok(fixture_text("netsh_wlan_networks.txt"))
            return ok(fixture_text("netsh_wlan_interfaces.txt"))

        ctx = CollectorContext(config=config, runner=CommandRunner(runner=runner), is_windows=True)
        r = WiFiCollector(ctx).collect()
        assert r.ok
        assert r.wifi_links[0].ssid == "HomeNet"
        # AP observations from BSSIDs (associated + visible)
        macs = {o.mac for o in r.observations}
        assert "001122334455" in macs
        assert "0c83cc112233" in macs

    def test_unavailable_on_linux(self, config):
        ctx = make_ctx(config, {}, is_windows=False)
        r = WiFiCollector(ctx).collect()
        assert r.ok is False
        assert "Windows" in (r.unavailable_reason or "")

    def test_no_wifi_adapter(self, config):
        ctx = make_ctx(config, {"netsh": ok("There are 0 interfaces on the system.")}, is_windows=True)
        r = WiFiCollector(ctx).collect()
        assert r.ok is False


class TestFlowCollector:
    def test_disabled(self, config):
        config.set("traffic.enabled", False)
        ctx = make_ctx(config, {})
        r = FlowCollector(ctx).collect()
        assert r.ok is False

    def test_netstat_fallback(self, config):
        def runner(args):
            if args[0] == "netstat":
                return ok(fixture_text("netstat_ano.txt"))
            return CommandResult(False, "", "", -1, error="no psutil")

        ctx = CollectorContext(config=config, runner=CommandRunner(runner=runner), is_windows=True)
        c = FlowCollector(ctx)
        rows = c._netstat_fallback()
        assert rows and any(getattr(r.laddr, "ip", None) == "192.168.1.50" for r in rows)

    def test_psutil_path_produces_metadata_only(self, config):
        ctx = make_ctx(config, {})
        r = FlowCollector(ctx).collect()
        # may fail without psutil perms in some envs; if ok, flows must have no payload fields
        for f in r.flows:
            assert set(vars(f).keys()) <= {
                "src_ip", "dst_ip", "src_port", "dst_port", "protocol",
                "ts", "bytes", "duration_s", "source", "device_id", "state",
            }


class TestHealthCollector:
    def test_all_components(self, config):
        ctx = make_ctx(config, {})
        ctx.state["gateway_ip"] = "192.168.1.1"
        ctx.state["dhcp_server"] = "192.168.1.1"
        ctx.state["lease_expires"] = "2099-01-01 00:00:00"
        ctx.state["known_device_counts"] = {"total": 10, "online": 8}

        pinger = Pinger(runner=lambda t, s: PingResult(t, True, latency_ms=3.0))

        def http_ok(req, timeout=None):
            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, *a):
                    return b"ok"

            return R()

        c = HealthCollector(ctx, pinger=pinger, http_opener=http_ok)
        r = c.collect()
        names = {h.name: h for h in r.health.components}
        assert names["gateway_latency"].status == "ok"
        assert names["internet"].status == "ok"
        assert names["dhcp"].status == "ok"
        assert names["devices"].status == "ok"
        assert r.health.overall == "healthy"

    def test_degraded_explanations(self, config):
        ctx = make_ctx(config, {})
        ctx.state["gateway_ip"] = "192.168.1.1"
        pinger = Pinger(runner=lambda t, s: PingResult(t, False, error="timeout"))

        def http_fail(req, timeout=None):
            raise OSError("network unreachable")

        c = HealthCollector(ctx, pinger=pinger, http_opener=http_fail)
        r = c.collect()
        names = {h.name: h for h in r.health.components}
        assert names["gateway_latency"].status == "down"
        assert names["gateway_latency"].explanation  # explains, does not invent
        assert names["internet"].status == "down"
        assert r.health.overall in ("critical", "degraded")

    def test_local_only_mode_skips_external_probes(self, config):
        config.set("privacy.local_only_mode", True)
        ctx = make_ctx(config, {})
        calls = []

        def http_probe(req, timeout=None):
            calls.append(req)
            raise OSError("must not be called")

        c = HealthCollector(ctx, pinger=Pinger(runner=lambda t, s: PingResult(t, True, 1.0)), http_opener=http_probe)
        r = c.collect()
        names = {h.name: h for h in r.health.components}
        assert names["internet"].measured is False
        assert calls == []


class TestRouterCollector:
    def test_gateway_only(self, config):
        ctx = make_ctx(config, {})
        ctx.state["gateway_ip"] = "192.168.1.1"
        r = RouterCollector(ctx, upnp=_NoUpnp()).collect()
        assert r.router.get("gateway_ip") == "192.168.1.1"
        assert any(o.ipv4 == "192.168.1.1" and o.get("category") == "router" for o in r.observations)

    def test_api_snapshot_and_leases(self, config):
        config.set("router.api_enabled", True)
        config.set("router.base_url", "https://router.lan")
        payload = {
            "router_ip": "192.168.1.1",
            "lan_subnet": "192.168.1.0/24",
            "dhcp_range_start": "192.168.1.20",
            "dhcp_range_end": "192.168.1.150",
            "wan_status": "Connected",
            "model": "SimRouter 3000",
            "vendor": "Sim Networks",
            "dns_servers": ["1.1.1.1", "8.8.8.8"],
            "connected_clients": 2,
            "dhcp_leases": [
                {"ip": "192.168.1.20", "mac": "aa:bb:cc:dd:ee:ff", "hostname": "phone", "wireless": True, "lease_end": "2026-09-25T20:00:00Z"},
                {"ip": "192.168.1.21", "mac": "11:22:33:44:55:66", "hostname": "pc", "wireless": False},
            ],
        }

        def opener(req, timeout=None):
            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, *a):
                    return json.dumps(payload).encode()

            return R()

        ctx = make_ctx(config, {}, state={"gateway_ip": "192.168.1.1"})
        r = RouterCollector(ctx, upnp=_NoUpnp(), http_opener=opener).collect()
        assert r.router.get("model") == "SimRouter 3000"
        leases = r.router.get("dhcp_leases")
        assert len(leases) == 2
        wifi_lease = [o for o in r.observations if o.mac == "aabbccddeeff"][0]
        assert wifi_lease.get("connection_type") == "wifi"
        wired_lease = [o for o in r.observations if o.mac == "112233445566"][0]
        assert wired_lease.get("connection_type") == "ethernet"

    def test_api_failure_is_graceful(self, config):
        config.set("router.api_enabled", True)
        config.set("router.base_url", "https://router.lan")

        def opener(req, timeout=None):
            raise TimeoutError("router busy")

        ctx = make_ctx(config, {}, state={"gateway_ip": "192.168.1.1"})
        r = RouterCollector(ctx, upnp=_NoUpnp(), http_opener=opener).collect()
        assert r.router is not None
        assert "TimeoutError" in (r.router.get("api_error") or "")

    def test_no_sources(self, config):
        config.set("router.upnp_enabled", False)
        ctx = make_ctx(config, {})
        r = RouterCollector(ctx, upnp=_NoUpnp()).collect()
        assert r.ok is False
        assert r.unavailable_reason


class _NoUpnp:
    def query(self):
        return None


class TestSimulatedProvider:
    def test_scenario_evolution(self):
        p = SimulatedNetworkProvider()
        t0 = p.tick(0.0)
        assert len(t0["flows"]) > 0
        assert t0["router"].get("dhcp_leases")
        # IP change after t=900
        mac = SimulatedNetworkProvider.IP_CHANGE_MAC
        before = [d for d in t0["devices"] if d["mac"] == mac][0]
        after = [d for d in p.tick(1000.0)["devices"] if d["mac"] == mac][0]
        assert before["ipv4"] != after["ipv4"]
        assert after["ipv4"] == SimulatedNetworkProvider.IP_CHANGE_NEW
        # new device only after t=1200
        new_mac = SimulatedNetworkProvider.NEW_DEVICE.mac
        assert not any(d["mac"] == new_mac and d["online"] for d in t0["devices"])
        assert any(d["mac"] == new_mac and d["online"] for d in p.tick(1300.0)["devices"])

    def test_deterministic(self):
        a = SimulatedNetworkProvider(seed=7).tick(100.0)
        b = SimulatedNetworkProvider(seed=7).tick(100.0)
        assert [d["mac"] for d in a["devices"]] == [d["mac"] for d in b["devices"]]

    def test_collector_end_to_end(self, config):
        ctx = make_ctx(config, {})
        c = SimulatedCollector(ctx, provider=SimulatedNetworkProvider())
        r = c.collect()
        assert len(r.observations) >= 12
        assert r.router.get("wan_status") == "Connected"
        assert r.health.overall in ("healthy", "degraded")
        assert r.wifi_links and r.wifi_links[0].ssid == "HomeNet"
