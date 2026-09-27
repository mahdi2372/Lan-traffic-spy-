"""UPnP IGD client: SSDP discovery + WAN service SOAP queries.

Authorized mechanism only: UPnP is a service the router itself publishes on the
LAN. No credentials, no login attempts, no unauthorized access of any kind.
Requests are limited to the discovered gateway device description.
"""

from __future__ import annotations

import logging
import socket
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("lanwatcher.upnp")

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: {st}\r\n"
    "\r\n"
)
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:WANConnectionDevice:1",
    "upnp:rootdevice",
)


@dataclass
class UpnpService:
    service_type: str
    control_url: str


@dataclass
class UpnpDevice:
    location: str
    base_url: str
    friendly_name: Optional[str] = None
    manufacturer: Optional[str] = None
    model_name: Optional[str] = None
    model_number: Optional[str] = None
    services: list = field(default_factory=list)


def _local_ip_toward(target: str) -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect((target, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def ssdp_discover(timeout_s: float = 2.0) -> list:
    """Return LOCATION URLs of UPnP root devices found on the LAN."""
    locations: list = []
    seen = set()
    for st in SEARCH_TARGETS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.settimeout(timeout_s)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.sendto(SSDP_MSEARCH.format(st=st).encode("utf-8"), (SSDP_ADDR, SSDP_PORT))
            try:
                while True:
                    data, _addr = sock.recvfrom(2048)
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.lower().startswith("location:"):
                            loc = line.split(":", 1)[1].strip()
                            if loc not in seen:
                                seen.add(loc)
                                locations.append(loc)
            except socket.timeout:
                pass
            finally:
                sock.close()
        except OSError:
            continue
    return locations


def fetch_device_description(location: str, timeout_s: float = 3.0) -> Optional[UpnpDevice]:
    """Fetch + parse UPnP device description XML. Only http(s) to LAN hosts is allowed."""
    from urllib.parse import urlparse

    parsed = urlparse(location)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    try:
        host_ip = socket.gethostbyname(parsed.hostname)
    except OSError:
        return None
    try:  # only talk to private/link-local addresses (LAN devices)
        addr = socket.inet_aton(host_ip)
        first_octet = addr[0]
        if not (first_octet in (10, 127, 169, 172, 192) or host_ip.startswith("192.168.")):
            if first_octet not in (10,) and not (first_octet == 172 and 16 <= addr[1] <= 31) and not (first_octet == 192 and addr[1] == 168):
                return None
    except OSError:
        return None

    try:
        with urllib.request.urlopen(location, timeout=timeout_s) as resp:
            body = resp.read(256 * 1024)
    except Exception:  # noqa: BLE001 - network failure
        return None

    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    ns = {"u": "urn:schemas-upnp-org:device-1-0"}
    dev = root.find(".//u:device", ns)
    if dev is None:
        return None
    base = location.rsplit("/", 1)[0] + "/"

    def text(tag: str) -> Optional[str]:
        el = dev.find(f"u:{tag}", ns)
        return el.text.strip() if el is not None and el.text else None

    device = UpnpDevice(
        location=location,
        base_url=base,
        friendly_name=text("friendlyName"),
        manufacturer=text("manufacturer"),
        model_name=text("modelName"),
        model_number=text("modelNumber"),
    )
    for svc in root.findall(".//u:serviceList/u:service", ns):
        st = svc.find("u:serviceType", ns)
        cu = svc.find("u:controlURL", ns)
        if st is not None and cu is not None:
            control = cu.text or ""
            if control.startswith("/"):
                control = location.split("//", 1)[0] + "//" + location.split("//", 1)[1].split("/", 1)[0] + control
            elif not control.startswith("http"):
                control = base + control
            device.services.append(UpnpService(service_type=st.text or "", control_url=control))
    return device


def _soap(host_control_url: str, service_type: str, action: str, args: Optional[dict] = None, timeout_s: float = 3.0) -> Optional[dict]:
    from urllib.parse import urlparse
    import urllib.error

    params = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{service_type}">{params}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode("utf-8")
    req = urllib.request.Request(
        host_control_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service_type}#{action}"',
            "User-Agent": "LAN-Watcher/1.0 (UPnP client)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = resp.read(128 * 1024)
    except Exception:  # noqa: BLE001
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    out = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag and el.text and tag not in ("Envelope", "Body") and not tag.endswith("Response"):
            out[tag] = el.text.strip()
    return out or None


class UpnpRouterClient:
    """Queries WAN status/uptime/link properties from the gateway's UPnP services."""

    def __init__(self, timeout_s: float = 3.0):
        self.timeout_s = timeout_s

    def query(self) -> Optional[dict]:
        locations = ssdp_discover(timeout_s=min(2.0, self.timeout_s))
        for loc in locations:
            dev = fetch_device_description(loc, timeout_s=self.timeout_s)
            if dev is None:
                continue
            info = {
                "source": "upnp",
                "location": loc,
                "friendly_name": dev.friendly_name,
                "manufacturer": dev.manufacturer,
                "model": " ".join(x for x in (dev.model_name, dev.model_number) if x) or None,
            }
            wan_conn = next((s for s in dev.services if "WANIPConnection" in s.service_type or "WANPPPConnection" in s.service_type), None)
            wan_common = next((s for s in dev.services if "WANCommonInterfaceConfig" in s.service_type), None)
            if wan_conn:
                st = _soap(wan_conn.control_url, wan_conn.service_type, "GetStatusInfo", timeout_s=self.timeout_s)
                if st:
                    info["wan_status"] = st.get("NewConnectionStatus")
                    info["wan_error"] = st.get("NewLastConnectionError")
                    uptime = st.get("NewUptime")
                    if uptime and str(uptime).isdigit():
                        info["uptime_s"] = float(uptime)
                ip = _soap(wan_conn.control_url, wan_conn.service_type, "GetExternalIPAddress", timeout_s=self.timeout_s)
                if ip:
                    info["wan_ip"] = ip.get("NewExternalIPAddress")
            if wan_common:
                link = _soap(wan_common.control_url, wan_common.service_type, "GetCommonLinkProperties", timeout_s=self.timeout_s)
                if link:
                    info["wan_access_type"] = link.get("NewWANAccessType")
                    rs = link.get("NewLayer1UpstreamMaxBitRate")
                    rd = link.get("NewLayer1DownstreamMaxBitRate")
                    if rs and str(rs).isdigit():
                        info["wan_upstream_bps"] = int(rs)
                    if rd and str(rd).isdigit():
                        info["wan_downstream_bps"] = int(rd)
                    info["physical_link"] = link.get("NewPhysicalLinkStatus")
            if len(info) > 4:  # something useful beyond names
                return info
        return None
