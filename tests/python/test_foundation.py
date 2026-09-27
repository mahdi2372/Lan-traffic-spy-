"""Foundation tests: utils, config, secure store, event bus."""

from __future__ import annotations

import os

from lanwatcher.config import Config, DEFAULTS
from lanwatcher.events import EventBus
from lanwatcher.secure import SecureStore
from lanwatcher.util import (
    format_bps,
    format_bytes,
    iso,
    mac_display,
    normalize_mac,
    parse_iso,
    seconds_since,
    is_valid_ipv4,
    is_valid_ipv6,
    ip_family,
)


class TestMac:
    def test_normalize_colon(self):
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aabbccddeeff"

    def test_normalize_dash_and_cisco(self):
        assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aabbccddeeff"
        assert normalize_mac("aabb.ccdd.eeff") == "aabbccddeeff"

    def test_invalid(self):
        assert normalize_mac("") is None
        assert normalize_mac(None) is None
        assert normalize_mac("not-a-mac") is None
        assert normalize_mac("00:00:00:00:00:00") is None

    def test_display_roundtrip(self):
        assert mac_display("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


class TestIps:
    def test_validation(self):
        assert is_valid_ipv4("192.168.1.10")
        assert not is_valid_ipv4("999.1.1.1")
        assert is_valid_ipv6("fe80::1")
        assert not is_valid_ipv6("192.168.1.1")
        assert ip_family("10.0.0.1") == "ipv4"
        assert ip_family("::1") == "ipv6"


class TestTime:
    def test_iso_roundtrip(self):
        s = iso()
        assert s.endswith("Z")
        dt = parse_iso(s)
        assert dt is not None
        assert iso(dt) == s

    def test_seconds_since(self):
        assert seconds_since("not-a-time") == float("inf")


class TestFormat:
    def test_bps(self):
        assert format_bps(0) == "0 bps"
        assert "Mbps" in format_bps(94_000_000)

    def test_bytes(self):
        assert format_bytes(None) == "—"
        assert format_bytes(512) == "512 B"
        assert "MB" in format_bytes(5 * 1024 * 1024)


class TestConfig:
    def test_defaults_and_roundtrip(self, tmp_path):
        c = Config(tmp_path / "config.json")
        assert c.get("general.scan_interval_s") == DEFAULTS["general"]["scan_interval_s"]
        c.set("general.scan_interval_s", 15)
        c2 = Config(tmp_path / "config.json")
        assert c2.get("general.scan_interval_s") == 15

    def test_sanitization(self, tmp_path):
        c = Config(tmp_path / "config.json")
        c.set("general.scan_interval_s", 1)  # clamped to minimum 5
        assert c.get("general.scan_interval_s") == 5

    def test_local_only_disables_internet_probe(self, tmp_path):
        c = Config(tmp_path / "config.json")
        c.set("privacy.local_only_mode", True)
        assert c.get("privacy.internet_probe_enabled") is False

    def test_telemetry_always_off(self, tmp_path):
        c = Config(tmp_path / "config.json")
        c.set("privacy.telemetry_enabled", True)
        assert c.get("privacy.telemetry_enabled") is False

    def test_change_listener(self, tmp_path):
        c = Config(tmp_path / "config.json")
        seen = []
        c.on_change(lambda k, v: seen.append((k, v)))
        c.set("ui.theme", "light")
        assert ("ui.theme", "light") in seen


class TestSecureStore:
    def test_protect_roundtrip(self, tmp_path):
        store = SecureStore(tmp_path)
        token = store.protect("hunter2-not-a-real-secret")
        assert token and not token.startswith("hunter2")
        assert store.unprotect(token) == "hunter2-not-a-real-secret"

    def test_plain_passthrough(self, tmp_path):
        store = SecureStore(tmp_path)
        assert store.unprotect("plain-value") == "plain-value"

    def test_corrupt_token(self, tmp_path):
        store = SecureStore(tmp_path)
        assert store.unprotect("aes:00") is None


class TestEventBus:
    def test_pubsub_and_unsubscribe(self):
        bus = EventBus()
        got = []
        un = bus.subscribe("t", lambda ev: got.append(ev.payload))
        bus.publish("t", 1)
        un()
        bus.publish("t", 2)
        assert got == [1]

    def test_wildcard(self):
        bus = EventBus()
        got = []
        bus.subscribe("*", lambda ev: got.append(ev.topic))
        bus.publish("a", None)
        bus.publish("b", None)
        assert got == ["a", "b"]

    def test_subscriber_isolation(self):
        bus = EventBus()

        def boom(ev):
            raise RuntimeError("boom")

        got = []
        bus.subscribe("t", boom)
        bus.subscribe("t", lambda ev: got.append(ev.payload))
        bus.publish("t", 42)  # must not raise
        assert got == [42]
