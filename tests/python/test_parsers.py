"""Parser tests against golden captured outputs (Windows + Linux formats)."""

from __future__ import annotations

from tests.python.conftest import fixture_text

from lanwatcher.parsing.arp import parse_arp_a, parse_ip_neigh, parse_netsh_neighbors
from lanwatcher.parsing.ipconfig import parse_ipconfig_all, parse_windows_datetime
from lanwatcher.parsing.netsh import (
    channel_to_freq,
    parse_netsh_admin_interfaces,
    parse_netsh_firewall_profile,
    parse_netsh_ip_interfaces,
    parse_wlan_interfaces,
    parse_wlan_networks,
)
from lanwatcher.parsing.netstat import parse_netstat_ano
from lanwatcher.parsing.oui import OuiLookup
from lanwatcher.parsing.powershell import (
    classify_interface,
    parse_link_speed,
    parse_net_adapter_json,
)
from lanwatcher.parsing.route import parse_route_print


class TestArp:
    def test_windows_arp(self):
        entries = parse_arp_a(fixture_text("arp_windows.txt"))
        by_ip = {e.ip: e for e in entries}
        assert by_ip["192.168.1.1"].mac == "001122334455"
        assert by_ip["192.168.1.1"].state == "dynamic"
        assert by_ip["192.168.1.1"].iface_ip == "192.168.1.50"
        assert by_ip["192.168.1.20"].mac == "a4bb6d1f8877"
        assert "10.0.0.1" in by_ip  # second interface block

    def test_linux_arp(self):
        entries = parse_arp_a(fixture_text("arp_linux.txt"))
        by_ip = {e.ip: e for e in entries}
        assert by_ip["192.168.1.1"].mac == "001122334455"
        assert by_ip["192.168.1.1"].interface == "eth0"
        assert by_ip["192.168.1.30"].mac is None  # <incomplete>

    def test_ip_neigh(self):
        entries = parse_ip_neigh(fixture_text("ip_neigh.txt"))
        by_ip = {e.ip: e for e in entries}
        assert by_ip["192.168.1.20"].mac == "a4bb6d1f8877"
        assert by_ip["192.168.1.20"].state == "stale"
        assert "192.168.1.99" not in by_ip  # FAILED entries are dropped


class TestNetshNeighbors:
    def test_v4(self):
        entries = parse_netsh_neighbors(fixture_text("netsh_neighbors_v4.txt"))
        by_ip = {e.ip: e for e in entries}
        assert by_ip["192.168.1.1"].interface == "Wi-Fi"
        assert by_ip["192.168.1.1"].mac == "001122334455"
        assert by_ip["10.0.0.1"].interface == "Ethernet"

    def test_v6(self):
        entries = parse_netsh_neighbors(fixture_text("netsh_neighbors_v6.txt"), family="ipv6")
        ips = {e.ip for e in entries}
        assert "fe80::1111:2222:3333:4444" in ips
        by_ip = {e.ip: e for e in entries}
        assert by_ip["fe80::aaaa:bbbb:cccc:dddd"].mac == "a4bb6d1f8877"


class TestIpconfig:
    def test_datetime(self):
        assert parse_windows_datetime("Thursday, September 24, 2026 8:15:03 PM") == "2026-09-24 20:15:03"
        assert parse_windows_datetime("Friday, September 25, 2026 8:15:03 AM") == "2026-09-25 08:15:03"
        assert parse_windows_datetime("garbage") is None

    def test_full_parse(self):
        r = parse_ipconfig_all(fixture_text("ipconfig_all.txt"))
        assert r.hostname == "DESKTOP-7QK2M1A"
        assert len(r.adapters) == 3
        by_name = {a.name: a for a in r.adapters}
        assert set(by_name) == {"Wi-Fi", "Ethernet", "Ethernet 2"}

        wifi = by_name["Wi-Fi"]
        assert wifi.mac == "a4bb6d1f8877"
        assert wifi.ipv4 == "192.168.1.50"
        assert wifi.subnet_mask == "255.255.255.0"
        assert wifi.gateways == ["192.168.1.1"]
        assert wifi.dhcp_server == "192.168.1.1"
        assert wifi.dhcp_enabled is True
        assert wifi.dns_servers == ["192.168.1.1", "1.1.1.1"]  # continuation line
        assert wifi.lease_obtained == "2026-09-24 20:15:03"
        assert wifi.lease_expires == "2026-09-25 20:15:03"
        assert wifi.ipv6 == ["fe80::1234:5678:9abc:def0"]  # link-local kept as-is (collectors filter)

        eth = by_name["Ethernet"]
        assert eth.dhcp_enabled is False
        assert eth.ipv4 == "10.0.0.2"
        assert eth.ipv6 == ["2001:db8::100", "fe80::aabb:ccff:feee:0011"]
        assert eth.mac == "b42e99aaccee"

        down = by_name["Ethernet 2"]
        assert down.media_state == "Media disconnected"


class TestWlan:
    def test_interfaces(self):
        links = parse_wlan_interfaces(fixture_text("netsh_wlan_interfaces.txt"))
        assert len(links) == 1
        w = links[0]
        assert w.ssid == "HomeNet"
        assert w.bssid == "001122334455"
        assert w.signal_pct == 92
        assert w.channel == 36
        assert w.radio_type == "802.11ax"
        assert w.receive_rate_mbps == 1201
        assert w.security == "WPA2-Personal"
        assert w.band == "5 GHz"
        assert w.frequency_mhz == 5180

    def test_networks(self):
        nets = parse_wlan_networks(fixture_text("netsh_wlan_networks.txt"))
        assert len(nets) == 3
        home = nets[0]
        assert home["ssid"] == "HomeNet"
        assert len(home["bssids"]) == 2
        assert home["bssids"][0]["channel"] == 36
        iot = nets[1]
        assert iot["bssids"][0]["channel"] == 6
        open_net = nets[2]
        assert open_net["authentication"] == "Open"

    def test_channel_to_freq(self):
        assert channel_to_freq(1) == 2412
        assert channel_to_freq(6) == 2437
        assert channel_to_freq(36) == 5180
        assert channel_to_freq(149) == 5745


class TestNetshInterfaces:
    def test_admin(self):
        rows = parse_netsh_admin_interfaces(fixture_text("netsh_interface_show.txt"))
        assert rows[0] == {"admin_state": "enabled", "state": "connected", "type": "dedicated", "name": "Wi-Fi"}
        assert rows[1]["state"] == "disconnected"
        assert len(rows) == 4

    def test_ip_interfaces(self):
        rows = parse_netsh_ip_interfaces(fixture_text("netsh_ip_interfaces.txt"))
        by_name = {r["name"]: r for r in rows}
        assert by_name["Wi-Fi"]["idx"] == 12
        assert by_name["Wi-Fi"]["mtu"] == 1500
        assert by_name["Ethernet"]["state"] == "disconnected"

    def test_firewall(self):
        fw = parse_netsh_firewall_profile(fixture_text("netsh_firewall.txt"))
        assert fw["enabled"] is True
        assert fw["state"] == "ON"
        assert "BlockInbound" in fw["policy"]


class TestNetstat:
    def test_parse(self):
        rows = parse_netstat_ano(fixture_text("netstat_ano.txt"))
        established = [r for r in rows if r.state == "ESTABLISHED"]
        assert len(established) == 3
        web = [r for r in rows if r.remote_ip == "8.8.8.8"][0]
        assert web.local_ip == "192.168.1.50"
        assert web.local_port == 52344
        assert web.remote_port == 443
        assert web.protocol == "TCP"
        assert web.pid == 8844
        lan = [r for r in rows if r.remote_ip == "192.168.1.20"][0]
        assert lan.remote_port == 445
        udp = [r for r in rows if r.protocol == "UDP"]
        assert udp[0].remote_ip is None
        v6 = [r for r in rows if r.local_ip.startswith("2001:db8")][0]
        assert v6.remote_port == 443


class TestRoute:
    def test_parse(self):
        t = parse_route_print(fixture_text("route_print.txt"))
        assert t.default_gateway == "192.168.1.1"
        assert t.default_interface == "192.168.1.50"
        assert len(t.interfaces) == 3
        assert t.interfaces[0]["mac"].replace(" ", "") == "a4bb6d1f8877"
        on_link = [r for r in t.routes if r.destination == "192.168.1.0"]
        assert on_link and on_link[0].gateway == "On-link"


class TestPowerShell:
    def test_link_speed(self):
        assert parse_link_speed("1 Gbps") == 1_000_000_000
        assert parse_link_speed("1.2 Gbps") == 1_200_000_000
        assert parse_link_speed("100 Mbps") == 100_000_000
        assert parse_link_speed(None) is None

    def test_classify(self):
        assert classify_interface("Intel(R) Wi-Fi 6 AX201", "Wi-Fi") == "wifi"
        assert classify_interface("Realtek Gaming GbE Family Controller", "Ethernet") == "ethernet"
        assert classify_interface("TAP-Windows Adapter V9", "Ethernet 2") == "other"
        assert classify_interface("Something Else", "vEthernet") == "other"
        assert classify_interface("Mystery Device", "mystery0") == "unknown"

    def test_net_adapter_json(self):
        ifaces = parse_net_adapter_json(fixture_text("get_net_adapter.json"))
        assert len(ifaces) == 3
        wifi = ifaces[0]
        assert wifi.name == "Wi-Fi"
        assert wifi.kind == "wifi"
        assert wifi.is_up == 1
        assert wifi.link_speed_bps == 1_200_000_000
        assert wifi.mac == "a4bb6d1f8877"
        assert ifaces[1].is_up == 0
        assert ifaces[2].kind == "other"


class TestOui:
    def test_builtin(self):
        oui = OuiLookup()
        assert oui.vendor("b827eb001122") == "Raspberry Pi Foundation"
        assert oui.vendor("30aea4001122") == "Espressif Inc."
        assert oui.vendor("ffffffffffff") is None or oui.vendor("ffffffffffff")

    def test_local_admin(self):
        oui = OuiLookup()
        assert "Locally administered" in (oui.vendor("020000000001") or "")

    def test_ieee_file(self, tmp_path):
        p = tmp_path / "oui.txt"
        p.write_text("AA-BB-CC   (hex)\t\tTEST VENDOR GMBH\n", encoding="utf-8")
        oui = OuiLookup(p)
        assert oui.vendor("aabbccddeeff") == "TEST VENDOR GMBH"
        assert oui.vendor("b827eb001122") == "Raspberry Pi Foundation"  # builtin kept
