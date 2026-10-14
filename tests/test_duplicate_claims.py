from __future__ import annotations

import time

from app import checks, store, views
from app.sync import write_clients
from app.unifi_adapter import UnifiClient

NOW = int(time.time())


def _c(mac, ip, *, fixed=None, online=False, name=None):
    return UnifiClient(mac, name, None, None, ip, fixed, None, None, NOW - 100, NOW, online)


def _dups(conn):
    return {
        (str(c.ip), d.name)
        for c in checks.run_checks(conn, 14, NOW).conflicts
        for d in c.devices
        if d.mac.startswith("00:00:00")
    }


def test_old_lease_of_a_reserved_device_is_not_a_conflict(conn):
    # the user's case: reserved at .58 but last used .56, another device reserved at .56
    with conn:
        write_clients(
            conn,
            [
                _c("00:00:00:00:00:01", "10.1.20.156", fixed="10.1.20.156", name="Laptop"),
                _c("00:00:00:00:00:02", "10.1.20.156", fixed="10.1.20.158", name="Laptop (Wired)"),
            ],
        )
    assert _dups(conn) == set()


def test_online_device_on_someone_elses_reservation_is_a_conflict(conn):
    with conn:
        write_clients(
            conn,
            [
                _c("00:00:00:00:00:01", "10.1.20.200", online=True, name="Live"),
                _c("00:00:00:00:00:02", "10.1.20.150", fixed="10.1.20.200", name="Reserved"),
            ],
        )
    assert _dups(conn) == {("10.1.20.200", "Live"), ("10.1.20.200", "Reserved")}


def test_two_offline_dynamic_leases_are_not_a_conflict(conn):
    with conn:
        write_clients(
            conn,
            [
                _c("00:00:00:00:00:01", "10.1.20.235", name="Old phone"),
                _c("00:00:00:00:00:02", "10.1.20.235", name="Other phone"),
            ],
        )
    assert _dups(conn) == set()


def test_reserved_offline_device_is_shown_at_its_reservation(conn):
    with conn:
        write_clients(conn, [_c("00:00:00:00:00:02", "10.1.20.156", fixed="10.1.20.158", name="W")])
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    row = next(r for r in views.vlan_detail(conn, vid).devices if r.device.name == "W")
    assert str(row.ip) == "10.1.20.158"
