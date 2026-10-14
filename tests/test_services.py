from __future__ import annotations

from ipaddress import IPv4Address as A

from app import store, views
from app.sync import write_clients
from app.unifi_adapter import UnifiClient


def _vlan(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name)


def test_vlan_overview_from_fixtures(conn):
    overview = views.vlan_overview(conn)
    assert [v.vlan.name for v in overview] == [
        "Default",
        "Core",
        "Devices",
        "IoT",
        "Video",
        "Management",
        "DMZ",
    ]
    video = next(v for v in overview if v.vlan.name == "Video")
    assert video.util.total == 254
    assert video.util.used >= 3  # gateway + cameras
    assert video.devices == []  # the rail shows utilisation only; no device lists built


def test_device_in_vlan_by_ip_even_when_network_id_differs(conn):
    v = _vlan(conn, "Core")
    stray = UnifiClient(
        mac="00:11:22:33:44:55",
        name="stray",
        hostname=None,
        vendor=None,
        ip="10.1.10.77",
        fixed_ip=None,
        note=None,
        network_id="not-a-real-id",
        first_seen=1,
        last_seen=2,
        online=False,
    )
    with conn:
        write_clients(conn, [stray])
    view = views.vlan_detail(conn, v.id)
    assert any(
        r.device.mac == "00:11:22:33:44:55" and r.ip == A("10.1.10.77") for r in view.devices
    )


def test_save_range_validation_and_overlap(conn):
    v = _vlan(conn, "Video")
    ok = store.save_range(conn, v.id, "cameras", "10.1.40.21", "10.1.40.60", "camera", "")
    assert ok.ok
    dup = store.save_range(conn, v.id, "cameras", "10.1.40.100", "10.1.40.110", "", "")
    assert "already exists" in dup.errors[0]
    overlap = store.save_range(conn, v.id, "nvr", "10.1.40.60", "10.1.40.70", "", "")
    assert overlap.errors and "overlaps range 'cameras'" in overlap.errors[0]
    outside = store.save_range(conn, v.id, "x", "10.1.41.1", "10.1.41.5", "", "")
    assert any("outside" in e for e in outside.errors)
    bad = store.save_range(conn, v.id, "x", "nope", "10.1.40.5", "", "")
    assert "invalid address" in bad.errors[0]
    assert len(store.list_ranges(conn, v.id)) == 1


def test_save_range_dhcp_warning_still_saves(conn):
    v = _vlan(conn, "Video")  # DHCP 10.1.40.232-235
    c = store.save_range(conn, v.id, "pool", "10.1.40.230", "10.1.40.240", "", "")
    assert c.ok and c.warnings and "DHCP" in c.warnings[0]
    assert len(store.list_ranges(conn, v.id)) == 1


def test_edit_range_excludes_itself_from_overlap(conn):
    v = _vlan(conn, "Video")
    store.save_range(conn, v.id, "cameras", "10.1.40.21", "10.1.40.60", "camera", "")
    r = store.list_ranges(conn, v.id)[0]
    c = store.save_range(
        conn, v.id, "cameras", "10.1.40.21", "10.1.40.80", "camera", "", range_id=r.id
    )
    assert c.ok
    assert store.get_range(conn, r.id).end == A("10.1.40.80")
    store.delete_range(conn, r.id)
    assert store.list_ranges(conn, v.id) == []


def test_next_free_skips_devices_and_gateway(conn):
    v = _vlan(conn, "Video")
    store.save_range(conn, v.id, "cams", "10.1.40.16", "10.1.40.20", "", "")
    # fixtures: 10.1.40.16, .17, .18 are cameras
    assert views.next_free(conn, v.id, "cams") == A("10.1.40.19")
    store.save_range(conn, v.id, "gw", "10.1.40.1", "10.1.40.2", "", "")
    assert views.next_free(conn, v.id, "gw") == A("10.1.40.2")


def test_build_cells_mark_every_host_address(conn):
    v = _vlan(conn, "Video")
    store.save_range(conn, v.id, "cams", "10.1.40.21", "10.1.40.60", "camera", "")
    store.update_device(conn, "e4:24:6c:bd:9c:67", "camera")  # 10.1.40.17, outside cams
    view = views.vlan_detail(conn, v.id)
    kinds = {str(c.ip): c.kind for c in view.cells}
    assert len(view.cells) == 254 and view.cells[0].ip == A("10.1.40.1")
    assert kinds["10.1.40.1"] == "gateway"
    assert kinds["10.1.40.17"] == "bad" and A("10.1.40.17") in view.bad_ips
    assert kinds["10.1.40.16"] == "used"
    assert kinds["10.1.40.21"] == "reserved"
    assert kinds["10.1.40.232"] == "dhcp"
    assert kinds["10.1.40.100"] == "free"
    store.update_device(conn, "e4:24:6c:bd:9c:67", "camera")
    store.save_range(conn, v.id, "cam2", "10.1.40.16", "10.1.40.18", "camera", "")
    view = views.vlan_detail(conn, v.id)
    kinds = {str(c.ip): c.kind for c in view.cells}
    assert kinds["10.1.40.17"] == "used-reserved" and not view.bad_ips
    # two ranges, two hues, labels under the strip in address order
    assert sorted(view.hues.values()) == [0, 1]
    assert [m.name for m in view.marks] == ["cam2", "cams", "DHCP pool"]
    assert view.marks[-1].hue is None and view.dhcp_size == 4 and view.dhcp_stats.used == 0
    assert {c.hue for c in view.cells if c.kind in ("reserved", "used-reserved")} == {0, 1}


def test_update_device_keeps_metadata_across_sync(conn, clients):
    store.update_device(conn, "B4:22:00:73:DC:5C", "printer")
    with conn:
        write_clients(conn, clients)  # a second sync must not clobber category/note
    d = store.get_device(conn, "b4:22:00:73:dc:5c")
    assert d.category == "printer"
    assert "printer" in store.categories(conn)
