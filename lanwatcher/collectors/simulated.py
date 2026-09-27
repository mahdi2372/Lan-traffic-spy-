"""Simulated network data provider for testing and demo mode.

Generates a believable, time-evolving LAN without touching any real network.
Deterministic for a given seed: devices, sessions, IP changes, a new device
arrival, flows, Wi-Fi details, router snapshot and health variations.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from ..models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    DeviceObservation,
    FlowObservation,
    HealthComponent,
    HealthSnapshot,
    InterfaceInfo,
    RouterSnapshot,
    WifiLinkInfo,
)
from ..util import epoch_to_iso, mac_display
from .base import Collector, CollectorResult


@dataclass
class SimDevice:
    mac: str
    ipv4: str
    hostname: str
    vendor: str
    connection_type: str
    category: str
    iface: str
    online_from: float = 0.0
    online_until: float = 3600.0
    latency: float = 3.0


class SimulatedNetworkProvider:
    """Pure-python scenario generator. `tick(t)` returns CollectorResults."""

    SCENARIO = [
        SimDevice("001122334455", "192.168.1.1", "home-router", "Cisco Systems, Inc", "unknown", "router", "sim0", 0, 3600, 1.2),
        SimDevice("a4bb6d1f8877", "192.168.1.50", "DESKTOP-7QK2M1A", "Apple, Inc.", "wifi", "computer", "Wi-Fi", 0, 3600, 2.5),
        SimDevice("b42e99aaccee", "192.168.1.51", "work-laptop", "Giga-Byte Technology Co., Ltd.", "ethernet", "computer", "Ethernet", 0, 3600, 1.8),
        SimDevice("f8ff0b123456", "192.168.1.20", "Toms-iPhone", "Apple, Inc.", "wifi", "phone", "Wi-Fi", 0, 3600, 8.0),
        SimDevice("30aea4001122", "192.168.1.30", "esp-sensor-1", "Espressif Inc.", "wifi", "iot", "Wi-Fi", 0, 3600, 14.0),
        SimDevice("00e04c681122", "192.168.1.40", "brother-printer", "Hewlett Packard (printer)", "ethernet", "printer", "Ethernet", 0, 3600, 4.0),
        SimDevice("b827eb009988", "192.168.1.60", "raspberrypi-nas", "Raspberry Pi Foundation", "ethernet", "server", "Ethernet", 0, 3600, 1.1),
        SimDevice("5c63bf778899", "192.168.1.21", "Dads-Android", "TP-LINK Technologies Co., Ltd.", "wifi", "phone", "Wi-Fi", 0, 3600, 12.0),
        SimDevice("e4f4c6aabbcc", "192.168.1.70", "living-room-tv", "NETGEAR", "wifi", "media", "Wi-Fi", 0, 2400, 22.0),
        SimDevice("d8a01d445566", "192.168.1.31", "smart-plug-2", "Espressif Inc.", "wifi", "iot", "Wi-Fi", 600, 3600, 18.0),
        SimDevice("98dac4a1b2c3", "192.168.1.80", "kids-tablet", "TP-LINK Technologies", "wifi", "tablet", "Wi-Fi", 300, 3600, 16.0),
        SimDevice("3c22fbbbccdd", "192.168.1.90", "guest-laptop", "Apple, Inc.", "wifi", "computer", "Wi-Fi", 1800, 3600, 9.0),
    ]
    # device that changes its IP at t=900
    IP_CHANGE_MAC = "f8ff0b123456"
    IP_CHANGE_NEW = "192.168.1.22"
    # new device arrives at t=1200
    NEW_DEVICE = SimDevice("0c83cc998877", "192.168.1.95", "unknown-device", "TP-LINK Technologies Co., Ltd.", "wifi", "unknown", "Wi-Fi", 1200, 3600, 11.0)

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def tick(self, t: float) -> dict:
        """Return {'devices': [...], 'flows': [...], ...} for time offset t seconds."""
        t = max(0.0, float(t))
        devices = list(self.SCENARIO) + [self.NEW_DEVICE]
        out = {
            "t": t,
            "devices": [],
            "flows": [],
            "wifi": None,
            "interfaces": [],
            "router": None,
            "health": None,
            "self_mac": "a4bb6d1f8877",
        }
        for d in devices:
            online = d.online_from <= t <= d.online_until
            jitter = self.rng.uniform(-0.3, 0.3)
            ip = d.ipv4
            if d.mac == self.IP_CHANGE_MAC and t >= 900:
                ip = self.IP_CHANGE_NEW
            out["devices"].append(
                {
                    "mac": d.mac,
                    "ipv4": ip if online else None,
                    "hostname": d.hostname,
                    "vendor": d.vendor,
                    "connection_type": d.connection_type,
                    "category": d.category,
                    "iface": d.iface,
                    "online": online,
                    "latency": max(0.3, d.latency + jitter) if online else None,
                }
            )

        # flows from online devices to internet destinations
        dests = [("8.8.8.8", 443), ("1.1.1.1", 53), ("140.82.121.4", 443), ("20.190.160.14", 443), ("192.168.1.1", 53)]
        for d in devices:
            if not (d.online_from <= t <= d.online_until):
                continue
            n = self.rng.randint(1, 4)
            for _ in range(n):
                dip, dport = self.rng.choice(dests)
                ip = d.ipv4
                if d.mac == self.IP_CHANGE_MAC and t >= 900:
                    ip = self.IP_CHANGE_NEW
                out["flows"].append(
                    FlowObservation(
                        src_ip=ip,
                        dst_ip=dip,
                        src_port=self.rng.randint(40000, 65000),
                        dst_port=dport,
                        protocol="tcp" if dport != 53 else "udp",
                        ts=epoch_to_iso(t),
                        bytes=self.rng.randint(200, 250_000),
                        duration_s=round(self.rng.uniform(0.1, 30.0), 1),
                        state="established" if dport != 53 else None,
                        source="simulated",
                    )
                )

        out["wifi"] = WifiLinkInfo(
            interface="Wi-Fi",
            ssid="HomeNet",
            bssid="001122334455",
            signal_pct=int(88 + 4 * self.rng.random()),
            channel=36,
            radio_type="802.11ax",
            receive_rate_mbps=1201,
            transmit_rate_mbps=1201,
            frequency_mhz=5180,
            band="5 GHz",
            security="WPA2-Personal",
            state="connected",
        )

        out["interfaces"] = [
            InterfaceInfo(
                name="Wi-Fi",
                description="Intel(R) Wi-Fi 6 AX201 160MHz",
                mac="a4bb6d1f8877",
                kind="wifi",
                is_up=1,
                link_speed_bps=1_201_000_000,
                mtu=1500,
                ipv4="192.168.1.50",
                ipv4_prefix=24,
                ipv6="2001:db8::50",
                gateway="192.168.1.1",
                dns="192.168.1.1,1.1.1.1",
                profile="lan",
                source="simulated",
            ),
            InterfaceInfo(
                name="Ethernet",
                description="Realtek Gaming GbE Family Controller",
                mac="b42e99aaccee",
                kind="ethernet",
                is_up=1,
                link_speed_bps=1_000_000_000,
                mtu=1500,
                ipv4="10.0.0.2",
                ipv4_prefix=24,
                gateway="10.0.0.1",
                dns="8.8.8.8",
                source="simulated",
            ),
        ]

        lease_rows = []
        for d in self.SCENARIO:
            if d.category != "router":
                lease_rows.append(
                    {
                        "ip": d.ipv4 if not (d.mac == self.IP_CHANGE_MAC and t >= 900) else self.IP_CHANGE_NEW,
                        "mac": d.mac,
                        "hostname": d.hostname,
                        "lease_start": epoch_to_iso(0),
                        "lease_end": epoch_to_iso(86400),
                        "interface": "wlan1" if d.connection_type == "wifi" else "lan1",
                        "wireless": d.connection_type == "wifi",
                        "vendor": d.vendor,
                    }
                )
        online_count = sum(1 for x in out["devices"] if x["online"])
        router = RouterSnapshot(source="router_api")
        router.set("router_ip", "192.168.1.1")
        router.set("gateway_ip", "192.168.1.1")
        router.set("hostname", "home-router")
        router.set("lan_subnet", "192.168.1.0/24")
        router.set("dhcp_range_start", "192.168.1.20")
        router.set("dhcp_range_end", "192.168.1.150")
        router.set("wan_ip", "203.0.113." + str(40 + int(t // 600) % 10))
        router.set("wan_status", "Connected")
        router.set("uptime_s", 86400.0 + t)
        router.set("model", "SimRouter 3000")
        router.set("vendor", "Sim Networks Inc.")
        router.set("dns_servers", "192.168.1.1,1.1.1.1")
        router.set("ipv6_gateway", "fe80::1")
        router.set("ipv6_wan", "2001:db8:1::1")
        router.set("connected_clients", online_count)
        router.set("lease_time_s", 43200.0)
        router.set("dhcp_leases", lease_rows)
        out["router"] = router

        loss = 0.0 if t < 1500 else (4.0 if t < 1800 else 0.0)  # brief degradation window
        gw_latency = 1.5 if loss == 0 else 45.0
        comps = [
            HealthComponent("gateway_latency", "ok" if loss == 0 else "degraded", f"{gw_latency:.1f} ms, {loss:.0f}% loss",
                            "" if loss == 0 else "The gateway replied slowly during the probe window (simulated).",
                            latency_ms=gw_latency, loss_pct=loss),
            HealthComponent("dns", "ok", "12 ms to resolve dns.msftncsi.com", latency_ms=12.0),
            HealthComponent("internet", "ok", "probe http://www.msftconnecttest.com/connecttest.txt (31 ms)", latency_ms=31.0),
            HealthComponent("dhcp", "ok", "lease valid for 8.0 h"),
            HealthComponent("local_network", "ok", "devices are answering probes"),
            HealthComponent("devices", "ok", f"{online_count}/{len(devices)} devices online"),
        ]
        out["health"] = HealthSnapshot(
            overall="healthy" if loss == 0 else "degraded",
            score=100 if loss == 0 else 62,
            components=comps,
        )
        return out


class SimulatedCollector(Collector):
    """Single collector that feeds the pipeline from SimulatedNetworkProvider."""

    name = "simulated"
    description = "Simulated network provider (demo/testing)"
    interval_s = 5.0

    def __init__(self, ctx, provider: Optional[SimulatedNetworkProvider] = None, t0: float = 0.0):
        super().__init__(ctx)
        self.provider = provider or SimulatedNetworkProvider()
        self.t0 = t0

    def collect(self) -> CollectorResult:
        import time

        t = getattr(self.ctx.state, "get", lambda *_: None)("sim_t")
        if t is None:
            t = self.ctx.state.get("sim_t")
        if t is None:
            t = time.time() - self._epoch_start()
        snap = self.provider.tick(float(t))
        self.ctx.state["sim_t"] = float(t) + 5.0

        result = CollectorResult(collector=self.name)
        for d in snap["devices"]:
            obs = DeviceObservation(source=self.name)
            if d.get("ipv4"):
                obs.set("ipv4", d["ipv4"], CONFIDENCE_HIGH)
            obs.set("mac", d["mac"], CONFIDENCE_HIGH)
            if d.get("hostname"):
                obs.set("hostname", d["hostname"], CONFIDENCE_HIGH)
            if d.get("vendor"):
                obs.set("vendor", d["vendor"], CONFIDENCE_MEDIUM)
            if d.get("connection_type") in ("wifi", "ethernet"):
                obs.set("connection_type", d["connection_type"], CONFIDENCE_HIGH)
            if d.get("iface"):
                obs.set("interface_name", d["iface"], CONFIDENCE_HIGH)
            if d.get("category"):
                obs.set("category", d["category"], CONFIDENCE_HIGH)
            obs.set("online", bool(d.get("online")), CONFIDENCE_HIGH)
            if d.get("latency") is not None:
                obs.set("latency_ms", round(float(d["latency"]), 2), CONFIDENCE_HIGH)
            obs.raw = {"is_self": d["mac"] == snap.get("self_mac")}
            result.observations.append(obs)

        result.flows = snap["flows"]
        if snap.get("wifi"):
            result.wifi_links = [snap["wifi"]]
        result.interfaces = snap.get("interfaces") or []
        result.router = snap.get("router")
        result.health = snap.get("health")
        result.system.update(
            {
                "hostname": "DESKTOP-7QK2M1A",
                "default_gateway": "192.168.1.1",
                "dhcp_server": "192.168.1.1",
                "dhcp_enabled": True,
                "firewall": {"state": "ON", "policy": "BlockInbound,AllowOutbound", "enabled": True},
                "lease_expires": epoch_to_iso(self.provider.tick(float(t))["t"] + 43200),
            }
        )
        self.ctx.state["gateway_ip"] = "192.168.1.1"
        self.ctx.state["sim_snapshot"] = snap
        return result

    @staticmethod
    def _epoch_start() -> float:
        import time

        if not hasattr(SimulatedCollector, "_t0"):
            SimulatedCollector._t0 = time.time()
        return SimulatedCollector._t0
