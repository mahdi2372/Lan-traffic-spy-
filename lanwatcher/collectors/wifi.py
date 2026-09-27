"""Wi-Fi collector: association + visible networks via `netsh wlan`.

Strictly metadata-only: SSID, BSSID, signal, channel, radio, rates, auth mode.
Never requests, reads or attempts to obtain Wi-Fi passwords/keys.
"""

from __future__ import annotations

from ..models import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM, DeviceObservation
from ..parsing.netsh import parse_wlan_interfaces, parse_wlan_networks
from .base import Collector, CollectorResult

_WIFI_AUTH_WIRED_HINTS = ()


class WiFiCollector(Collector):
    name = "wifi"
    description = "Wi-Fi adapter association + visible networks (no passwords)"
    interval_s = 30.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        if not self.ctx.is_windows:
            result.unavailable_reason = "Wi-Fi API requires Windows (netsh wlan)"
            result.ok = False
            return result

        res = self.ctx.runner.run(["netsh", "wlan", "show", "interfaces"])
        links = []
        if res.ok:
            links = parse_wlan_interfaces(res.stdout)
        if not links:
            result.unavailable_reason = "Wi-Fi API unavailable (no wireless adapter or service stopped)"
            result.ok = False
            return result

        result.wifi_links = links
        for w in links:
            if w.interface:
                self.ctx.state.setdefault("wifi_iface", w.interface)
                self.ctx.state.setdefault("wifi_iface_mac", None)
            if w.bssid:
                # The associated BSSID is an access point radio on the LAN.
                obs = DeviceObservation(source=self.name)
                obs.set("mac", w.bssid, CONFIDENCE_HIGH)
                obs.set("category", "ap", CONFIDENCE_MEDIUM)
                obs.set("connection_type", "wifi", CONFIDENCE_HIGH)
                obs.set("online", True, CONFIDENCE_HIGH)
                obs.raw = {"ssid": w.ssid, "role": "associated-ap"}
                result.observations.append(obs)
            if w.interface:
                self.ctx.state["wifi_iface"] = w.interface
                self.ctx.state["wifi_bssid"] = w.bssid
                self.ctx.state["wifi_connected"] = (w.state or "").lower() == "connected"

        res = self.ctx.runner.run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        if res.ok:
            nets = parse_wlan_networks(res.stdout)
            result.visible_networks = nets
            for net in nets:
                for b in net.get("bssids", []):
                    if not b.get("bssid"):
                        continue
                    obs = DeviceObservation(source=self.name)
                    obs.set("mac", b["bssid"], CONFIDENCE_HIGH)
                    obs.set("category", "ap", CONFIDENCE_LOW)
                    obs.raw = {"ssid": net.get("ssid"), "role": "visible-ap", "signal": b.get("signal_pct")}
                    result.observations.append(obs)
        return result
