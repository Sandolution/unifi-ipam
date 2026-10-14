from __future__ import annotations

import time

from app import checks, store
from app.sync import write_clients
from app.unifi_adapter import UnifiClient

NOW = int(time.time())
VIDEO_NET = "6828dfac4a6b661c7d5bdf5d"


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def _found(conn):
    report = checks.run_checks(conn, 14)
    return {(f.vlan.name, str(f.ip), f.device.display_name) for f in report.unplanned}


def test_vlan_without_ranges_is_not_checked(conn):
    assert not any(v == "Video" for v, _, _ in _found(conn))


def test_device_outside_every_range_is_listed(conn):
    store.save_range(conn, _vid(conn, "Video"), "cams", "10.1.40.16", "10.1.40.18", "", "")
    video = {(ip, name) for v, ip, name in _found(conn) if v == "Video"}
    assert video == {("10.1.40.8", "Argus")}  # .16-.18 are inside the range


def test_dhcp_pool_and_old_leases_do_not_count(conn):
    store.save_range(conn, _vid(conn, "Video"), "cams", "10.1.40.16", "10.1.40.18", "", "")
    with conn:
        write_clients(
            conn,
            [
                # online, in the DHCP pool
                UnifiClient(
                    "00:00:00:00:00:41",
                    "Lease",
                    None,
                    None,
                    "10.1.40.233",
                    None,
                    None,
                    VIDEO_NET,
                    1,
                    NOW,
                    True,
                ),
                # offline, old lease outside every range
                UnifiClient(
                    "00:00:00:00:00:42",
                    "Gone",
                    None,
                    None,
                    "10.1.40.100",
                    None,
                    None,
                    VIDEO_NET,
                    1,
                    2,
                    False,
                ),
            ],
        )
    names = {name for v, _, name in _found(conn) if v == "Video"}
    assert "Lease" not in names and "Gone" not in names


def test_unifi_gear_is_included(conn):
    store.save_range(conn, _vid(conn, "Core"), "servers", "10.1.10.2", "10.1.10.15", "", "")
    core = {name for v, _, name in _found(conn) if v == "Core"}
    assert {"USW-Pro-48-PoE", "U6+ (Living Room)"} <= core
    assert "Core | Proxmox" not in core  # .7 is inside


def test_page_section_counts_and_ignore(client, conn):
    store.save_range(conn, _vid(conn, "Video"), "cams", "10.1.40.16", "10.1.40.18", "", "")
    report = checks.run_checks(conn, 14)
    assert report.counts()["norange"] == (1, 0)
    page = client.get("/checks").text
    section = page.split('id="norange"')[1].split("</section>")[0]
    assert "Argus" in section and "10.1.40.8" in section
    assert 'href="#norange"' in page and "not in a range" in page
    mac = report.unplanned[0].device.mac
    client.post("/checks/ignore", data={"kind": "norange", "subject": mac})
    after = checks.run_checks(conn, 14)
    assert after.counts()["norange"] == (0, 1) and after.total == report.total - 1
