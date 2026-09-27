"""Ethernet/wired adapter details: link state, speed, addressing.

Windows: PowerShell Get-NetAdapter JSON (slow cadence, cached by scheduler).
Other: psutil interface stats.
"""

from __future__ import annotations

from ..parsing.powershell import parse_net_adapter_json
from .base import Collector, CollectorResult


class EthernetCollector(Collector):
    name = "ethernet"
    description = "Wired adapter link state and speed"
    interval_s = 60.0

    def collect(self) -> CollectorResult:
        result = CollectorResult(collector=self.name)
        if self.ctx.is_windows:
            res = self.ctx.runner.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-NetAdapter | Select-Object Name,InterfaceDescription,MacAddress,Status,LinkSpeed,"
                    "@{N='NlMtu';E={$_.NlMtu}} | ConvertTo-Json -Compress",
                ],
                timeout=20.0,
            )
            if res.ok and res.stdout.strip():
                adapters = parse_net_adapter_json(res.stdout)
                if adapters:
                    # enrich matching interface entries from windows_net
                    by_name = {i.name: i for i in result.interfaces}
                    for a in adapters:
                        result.system.setdefault("adapters", []).append(a.to_dict())
                    # merge into result as interface updates
                    result.interfaces = adapters
                    for a in adapters:
                        if a.kind == "ethernet" and a.is_up and a.link_speed_bps:
                            self.ctx.state.setdefault("eth_link_speed", a.link_speed_bps)
                    return result
            result.unavailable_reason = "PowerShell adapter query failed or timed out"
            result.ok = False
            return result

        try:
            import psutil
        except ImportError:
            result.ok = False
            result.unavailable_reason = "psutil is not installed"
            return result
        stats = psutil.net_if_stats()
        for name, st in stats.items():
            if name == "lo":
                continue
            from ..parsing.powershell import classify_interface

            kind = classify_interface(None, name)
            result.system.setdefault("adapters", []).append(
                {
                    "name": name,
                    "kind": kind,
                    "is_up": bool(st.isup),
                    "speed_bps": int(st.speed) * 1_000_000 if st.speed and st.speed > 0 else None,
                    "mtu": st.mtu,
                }
            )
        return result
