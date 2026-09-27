"""Parsers for OS network command output. Pure functions - unit tested with golden fixtures."""

from .arp import parse_arp_a, parse_ip_neigh, parse_netsh_neighbors
from .ipconfig import parse_ipconfig_all
from .netsh import (
    parse_netsh_admin_interfaces,
    parse_netsh_firewall_profile,
    parse_netsh_ip_interfaces,
    parse_wlan_interfaces,
    parse_wlan_networks,
)
from .netstat import parse_netstat_ano
from .oui import OuiLookup
from .powershell import parse_net_adapter_json
from .route import parse_route_print

__all__ = [
    "parse_arp_a",
    "parse_ip_neigh",
    "parse_ipconfig_all",
    "parse_netsh_admin_interfaces",
    "parse_netsh_firewall_profile",
    "parse_netsh_ip_interfaces",
    "parse_netsh_neighbors",
    "parse_wlan_interfaces",
    "parse_wlan_networks",
    "parse_netstat_ano",
    "OuiLookup",
    "parse_net_adapter_json",
    "parse_route_print",
]
