"""Windows network collector: ipconfig, routes, interface admin state, firewall,
DNS configuration, network profile. Cross-platform fallbacks via psutil."""

from __future__ import annotations

from ..models import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, DeviceObservation, InterfaceInfo
from ..parsing.ipconfig import parse_ipconfig_all
from ..parsing.netsh import (
    parse_netsh_admin_interfaces,
    parse_netsh_firewall_profile,
    parse_netsh_ip_interfaces,
)
from ..parsing.powershell import classify_interface
from ..parsing.route import parse_route_print
from ..util import host_label, is_link_local


class WindowsNetworkCollector:
    name = "windows_net"
    description = "Windows interfaces, routes, DNS, firewall, network profile"
    interval_s = 30.0
    requires_elevation = False

    def __init__(self, ctx):
        self.ctx = ctx

    def available(self):
        return True, None

    def collect(self):
        from .base import CollectorResult

        result = CollectorResult(collector=self.name)
        system = result.system

        if self.ctx.is_windows:
            self._collect_windows(result, system)
        else:
            self._collect_posix(result, system)

        self._collect_self_device(result, system)
        return result

    # ---------------------------------------------------------------- windows

    def _collect_windows(self, result, system):
        res = self.ctx.runner.run(["ipconfig", "/all"])
        if res.ok:
            cfg = parse_ipconfig_all(res.stdout)
            system["hostname"] = cfg.hostname or host_label()
            admin = {}
            res_admin = self.ctx.runner.run(["netsh", "interface", "show", "interface"])
            if res_admin.ok:
                for row in parse_netsh_admin_interfaces(res_admin.stdout):
                    admin[row["name"]] = row
            iface_idx = {}
            res_idx = self.ctx.runner.run(["netsh", "interface", "ipv4", "show", "interfaces"])
            if res_idx.ok:
                for row in parse_netsh_ip_interfaces(res_idx.stdout):
                    iface_idx[row["name"]] = row

            for a in cfg.adapters:
                name = a.name
                kind = classify_interface(a.description, name)
                admin_row = admin.get(name, {})
                is_up = 1 if (a.media_state is None and admin_row.get("state") != "disconnected") else 0
                if a.media_state and "disconnect" in a.media_state.lower():
                    is_up = 0
                prefix = None
                if a.subnet_mask:
                    prefix = sum(bin(int(o)).count("1") for o in a.subnet_mask.split(".")) if "." in a.subnet_mask else None
                iface = InterfaceInfo(
                    name=name,
                    description=a.description,
                    mac=a.mac,
                    kind=kind,
                    is_up=is_up,
                    mtu=(iface_idx.get(name) or {}).get("mtu"),
                    ipv4=a.ipv4,
                    ipv4_prefix=prefix,
                    ipv6=next((ip for ip in a.ipv6 if not is_link_local(ip)), a.ipv6[0] if a.ipv6 else None),
                    gateway=a.gateways[0] if a.gateways else None,
                    dns=",".join(a.dns_servers) if a.dns_servers else None,
                    profile=a.connection_suffix,
                    source=self.name,
                )
                result.interfaces.append(iface)
                if a.gateways and iface.kind != "other":
                    self.ctx.state.setdefault("gateway_ip", a.gateways[0])
                if a.dhcp_server:
                    system.setdefault("dhcp_server", a.dhcp_server)
                if a.lease_obtained:
                    system.setdefault("lease_obtained", a.lease_obtained)
                if a.lease_expires:
                    system.setdefault("lease_expires", a.lease_expires)
                if a.dhcp_enabled is not None:
                    system.setdefault("dhcp_enabled", a.dhcp_enabled)

        res = self.ctx.runner.run(["route", "print", "-4"])
        if res.ok:
            table = parse_route_print(res.stdout)
            system["default_gateway"] = table.default_gateway
            system["default_interface"] = table.default_interface
            system["route_count"] = len(table.routes)
            if table.default_gateway:
                self.ctx.state["gateway_ip"] = table.default_gateway
            obs = DeviceObservation(source=self.name)
            if table.default_gateway:
                obs.set("ipv4", table.default_gateway, CONFIDENCE_HIGH)
                obs.set("category", "router", CONFIDENCE_MEDIUM)
                obs.set("online", True, CONFIDENCE_HIGH)
                result.observations.append(obs)

        res = self.ctx.runner.run(["netsh", "advfirewall", "show", "currentprofile"])
        if res.ok:
            system["firewall"] = parse_netsh_firewall_profile(res.stdout)

        res = self.ctx.runner.run(["netsh", "interface", "show", "interface"])
        if res.ok:
            system["admin_interfaces"] = parse_netsh_admin_interfaces(res.stdout)
            profiles = {r["name"]: r["state"] for r in system["admin_interfaces"]}
            system["network_profiles"] = profiles

    # ---------------------------------------------------------------- posix

    def _collect_posix(self, result, system):
        try:
            import psutil
        except ImportError:
            result.ok = False
            result.unavailable_reason = "psutil is not installed"
            return
        system["hostname"] = host_label()
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        gateways = {}
        try:
            gws = psutil.net_if_stats  # placeholder to keep import shape
            import socket

            # default gateway via /proc/net/route (Linux) or `route -n`
            res = self.ctx.runner.run(["ip", "route", "show", "default"])
            if res.ok:
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if "via" in parts:
                        gateways["default"] = parts[parts.index("via") + 1]
                        self.ctx.state["gateway_ip"] = gateways["default"]
                        system["default_gateway"] = gateways["default"]
        except Exception:  # noqa: BLE001
            pass

        for name, st in stats.items():
            if name == "lo":
                continue
            mac = ipv4 = ipv6 = None
            prefix = None
            for a in addrs.get(name, []):
                if a.family.name == "AF_LINK" and a.address and not mac:
                    mac = a.address
                elif a.family == 2 and not ipv4:  # AF_INET
                    ipv4 = a.address
                    prefix = a.netmask.split("0").count("255") if a.netmask else None
                    try:
                        prefix = sum(bin(int(o)).count("1") for o in a.netmask.split(".")) if a.netmask and "." in a.netmask else prefix
                    except ValueError:
                        pass
                elif a.family.name == "AF_INET6" and not ipv6 and not is_link_local(a.address.split("%")[0]):
                    ipv6 = a.address.split("%")[0]
            kind = classify_interface(None, name)
            result.interfaces.append(
                InterfaceInfo(
                    name=name,
                    mac=mac,
                    kind=kind,
                    is_up=1 if st.isup else 0,
                    link_speed_bps=int(st.speed) * 1_000_000 if st.speed and st.speed > 0 else None,
                    mtu=st.mtu,
                    ipv4=ipv4,
                    ipv4_prefix=prefix,
                    ipv6=ipv6,
                    gateway=gateways.get("default"),
                    source=self.name,
                )
            )
        if gateways.get("default"):
            obs = DeviceObservation(source=self.name)
            obs.set("ipv4", gateways["default"], CONFIDENCE_HIGH)
            obs.set("category", "router", CONFIDENCE_MEDIUM)
            obs.set("online", True, CONFIDENCE_HIGH)
            result.observations.append(obs)

    # ---------------------------------------------------------------- self device

    def _collect_self_device(self, result, system):
        from ..util import mac_display

        up_ifaces = [i for i in result.interfaces if i.is_up and i.ipv4 and i.kind != "other"]
        if not up_ifaces:
            up_ifaces = [i for i in result.interfaces if i.is_up and i.ipv4]
        if not up_ifaces:
            return
        primary = up_ifaces[0]
        obs = DeviceObservation(source=self.name)
        obs.set("ipv4", primary.ipv4, CONFIDENCE_HIGH)
        if primary.ipv6:
            obs.set("ipv6", primary.ipv6, CONFIDENCE_HIGH)
        if primary.mac:
            obs.set("mac", primary.mac, CONFIDENCE_HIGH)
        obs.set("hostname", system.get("hostname"), CONFIDENCE_HIGH)
        obs.set("interface_name", primary.name, CONFIDENCE_HIGH)
        obs.set("connection_type", primary.kind if primary.kind in ("wifi", "ethernet") else "unknown",
                CONFIDENCE_HIGH if primary.kind in ("wifi", "ethernet") else CONFIDENCE_MEDIUM)
        obs.set("category", "computer", CONFIDENCE_MEDIUM)
        obs.set("online", True, CONFIDENCE_HIGH)
        obs.raw = {"is_self": True}
        result.observations.append(obs)
        system["self_ipv4"] = primary.ipv4
        system["self_mac"] = mac_display(primary.mac) if primary.mac else None
