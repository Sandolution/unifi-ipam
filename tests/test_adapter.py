from __future__ import annotations

import pytest

from app.unifi_adapter import UnifiError, parse_clients, parse_info, parse_networks
from tests.conftest import load


def test_networks_filters_to_lans(networks):
    names = [n.name for n in networks]
    assert names == ["Default", "Core", "Devices", "IoT", "Video", "DMZ", "Management"]
    assert "Internet 1" not in names and "WireGuard" not in names


def test_network_fields(networks):
    core = next(n for n in networks if n.name == "Core")
    assert core.vlan_tag == 10
    assert core.subnet == "10.1.10.0/24"
    assert core.gateway == "10.1.10.1"
    assert (core.dhcp_start, core.dhcp_end) == ("10.1.10.232", "10.1.10.235")


def test_default_lan_is_untagged_without_dhcp(networks):
    default = next(n for n in networks if n.name == "Default")
    assert default.vlan_tag is None
    assert default.dhcp_start is None and default.dhcp_end is None


def test_clients_merge_known_and_active(clients):
    assert len(clients) == 82
    assert sum(c.online for c in clients) == 43
    macs = [c.mac for c in clients]
    assert macs == sorted(macs) and all(m == m.lower() for m in macs)


def test_client_fields_from_rest_user(clients):
    printer = next(c for c in clients if c.mac == "b4:22:00:73:dc:5c")
    assert printer.name == "Printer (Brother)"
    assert printer.fixed_ip == "10.1.20.104"
    assert printer.vendor == "Brother Industries, LTD."
    assert printer.first_seen == 1702848104
    assert printer.network_id is not None


def test_active_client_gets_live_ip_and_network(clients):
    phone = next(c for c in clients if c.mac == "68:4a:e9:d4:e7:2e")
    assert phone.online is True
    assert phone.ip == "10.1.20.16"
    assert phone.network_id == "657f60b5232df83f9267f86b"
    assert phone.note == "Samsung A53"


def test_note_absent_becomes_none(clients):
    without = [c for c in clients if c.note is None]
    assert len(without) == 82 - 13


def test_fixed_ip_only_when_enabled():
    user = {
        "meta": {"rc": "ok"},
        "data": [
            {"mac": "AA:BB:CC:00:00:01", "use_fixedip": False, "fixed_ip": "10.0.0.5"},
        ],
    }
    sta = {"meta": {"rc": "ok"}, "data": []}
    (c,) = parse_clients(user, sta)
    assert c.fixed_ip is None and c.mac == "aa:bb:cc:00:00:01"


def test_info():
    assert parse_info(load("legacy_stat_sysinfo.json")).version == "10.6.106"


def test_bad_envelope_raises():
    with pytest.raises(UnifiError):
        parse_networks({"meta": {"rc": "error"}, "data": []})
    with pytest.raises(UnifiError):
        parse_networks({"something": "else"})
