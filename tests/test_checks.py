from __future__ import annotations

import time

from app import checks, store
from app.sync import write_clients
from app.unifi_adapter import UnifiClient

NOW = int(time.time())


def _vlan(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name)


def _client(mac, ip, *, fixed=None, last_seen=NOW, online=False, network_id=None, name=None):
    return UnifiClient(
        mac=mac,
        name=name,
        hostname=None,
        vendor=None,
        ip=ip,
        fixed_ip=fixed,
        note=None,
        network_id=network_id,
        first_seen=NOW - 100,
        last_seen=last_seen,
        online=online,
    )


def test_out_of_range_only_when_category_has_ranges(conn):
    video = _vlan(conn, "Video")
    # fixtures: cameras at 10.1.40.16-18. Reserve .21-.60 for "camera" and tag two devices.
    store.save_range(conn, video.id, "cameras", "10.1.40.21", "10.1.40.60", "camera", "")
    store.update_device(conn, "e4:24:6c:bd:9c:67", "camera")  # 10.1.40.17 -> outside
    store.update_device(conn, "b4:22:00:73:dc:5c", "printer")  # no printer range -> skip
    report = checks.run_checks(conn, stale_days=14, now=NOW)
    macs = [f.device.mac for f in report.out_of_range]
    assert macs == ["e4:24:6c:bd:9c:67"]
    assert "cameras (10.1.40.21-10.1.40.60)" in report.out_of_range[0].detail
    # Move the range to cover it: finding disappears.
    rid = store.list_ranges(conn, video.id)[0].id
    store.save_range(
        conn, video.id, "cameras", "10.1.40.16", "10.1.40.60", "camera", "", range_id=rid
    )
    assert checks.run_checks(conn, stale_days=14, now=NOW).out_of_range == []


def test_duplicate_ip_in_same_vlan(conn):
    with conn:
        write_clients(
            conn,
            [
                _client("00:00:00:00:00:01", "10.1.10.50", name="a", fixed="10.1.10.50"),
                _client("00:00:00:00:00:02", "10.1.10.50", name="b", online=True),
                _client("00:00:00:00:00:03", "10.1.20.50", name="c"),  # same host part, other VLAN
            ],
        )
    report = checks.run_checks(conn, stale_days=14, now=NOW)
    mine = [c for c in report.conflicts if str(c.ip) == "10.1.10.50"]
    assert [(c.vlan.name, sorted(d.name for d in c.devices)) for c in mine] == [
        ("Core", ["a", "b"])
    ]
    assert not any(str(c.ip) == "10.1.20.50" for c in report.conflicts)  # other VLAN


def test_stale_fixed_ip_devices(conn):
    with conn:
        write_clients(
            conn,
            [
                _client(
                    "00:00:00:00:00:11",
                    "10.1.10.60",
                    fixed="10.1.10.60",
                    last_seen=NOW - 20 * 86400,
                ),
                _client(
                    "00:00:00:00:00:12", "10.1.10.61", fixed="10.1.10.61", last_seen=NOW - 2 * 86400
                ),
                _client(
                    "00:00:00:00:00:13", "10.1.10.62", last_seen=NOW - 40 * 86400
                ),  # no fixed ip
                _client(
                    "00:00:00:00:00:14",
                    "10.1.10.63",
                    fixed="10.1.10.63",
                    last_seen=NOW - 40 * 86400,
                    online=True,
                ),
            ],
        )
    report = checks.run_checks(conn, stale_days=14, now=NOW)
    stale = [
        (f.device.mac, f.detail) for f in report.stale_fixed if f.device.mac.startswith("00:00:00")
    ]
    assert stale == [("00:00:00:00:00:11", "fixed IP, last seen 20d ago")]
    relaxed = checks.run_checks(conn, stale_days=30, now=NOW).stale_fixed
    assert all(f.device.mac != "00:00:00:00:00:11" for f in relaxed)


def test_fixtures_contain_real_findings(conn):
    """The live data has a host with two NICs on one IP and long-unseen fixed-IP devices."""
    report = checks.run_checks(conn, stale_days=10_000, now=NOW)
    assert report.out_of_range == []  # no categories assigned yet
    assert report.stale_fixed == []
    core = [c for c in report.conflicts if c.vlan.name == "Core"]
    assert [(str(c.ip), sorted(d.display_name for d in c.devices)) for c in core] == [
        ("10.1.10.7", ["Cronus", "Cronus (enp2s0)"])
    ]
    stale = checks.run_checks(conn, stale_days=14, now=1791718322).stale_fixed  # fixture date
    macs = [f.device.mac for f in stale]
    assert len(macs) == len(set(macs)) and len(macs) >= 10  # one finding per device
    assert all(f.ip in f.vlan.net for f in stale)


def test_checks_page_and_nav_count(client, conn):
    with conn:
        write_clients(
            conn,
            [
                _client("00:00:00:00:00:01", "10.1.10.50", name="a", fixed="10.1.10.50"),
                _client("00:00:00:00:00:02", "10.1.10.50", name="b", online=True),
            ],
        )
    r = client.get("/checks")
    assert r.status_code == 200
    assert "Address conflicts" in r.text and "10.1.10.50" in r.text
    n = checks.run_checks(conn, stale_days=14).total
    assert n >= 2
    assert f'class="count warn" title="{n} findings">{n}</span>' in r.text
    vlan_page = client.get("/", follow_redirects=True).text  # count on every page
    assert f'class="count warn" title="{n} findings">{n}</span>' in vlan_page
