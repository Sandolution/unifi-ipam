from __future__ import annotations

from ipaddress import IPv4Address as A

from app import models, store, views


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def test_zoom_on_range_filters_cells_and_devices(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "camera", "")
    rid = store.list_ranges(conn, vid)[0].id
    view = views.vlan_detail(conn, vid, str(rid))
    z = view.zoom
    assert z and (z.name, str(z.start), str(z.end), z.size) == (
        "cams",
        "10.1.40.16",
        "10.1.40.20",
        5,
    )
    assert [str(c.ip) for c in view.zoom_cells] == [f"10.1.40.{i}" for i in range(16, 21)]
    assert {str(r.ip) for r in view.shown} == {"10.1.40.16", "10.1.40.17", "10.1.40.18"}
    assert len(view.devices) > len(view.shown)
    assert z.used == 3 and z.next_free == A("10.1.40.19")
    assert z.window_style.startswith("left: calc(")


def test_zoom_on_dhcp_and_unknown_key(conn):
    vid = _vid(conn, "Video")
    view = views.vlan_detail(conn, vid, "dhcp")
    assert view.zoom.name == "DHCP pool" and view.zoom.size == 4 and view.zoom.next_free is None
    view = views.vlan_detail(conn, vid, "9999")
    assert view.zoom is None and view.shown == view.devices


def test_marks_carry_zoom_keys(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "camera", "")
    rid = store.list_ranges(conn, vid)[0].id
    keys = [m.key for m in views.vlan_detail(conn, vid).marks]
    assert keys == [str(rid), "dhcp"]


def test_device_inherits_range_category_unless_overridden(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "Camera", "")
    store.update_device(conn, "e4:24:6c:bd:9c:67", "Doorbell")  # own category wins
    rows = {str(r.ip): r for r in views.vlan_detail(conn, vid).devices}
    driveway, garden = rows["10.1.40.16"], rows["10.1.40.17"]
    assert driveway.inherited == "Camera" and driveway.category == "Camera"
    assert driveway.device.category == ""  # not stored
    assert garden.inherited is None and garden.category == "Doorbell"
    outside = rows["10.1.40.8"]  # Argus, outside the range
    assert outside.inherited is None and outside.category == ""
    # range category change follows through
    rid = store.list_ranges(conn, vid)[0].id
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "CCTV", "", range_id=rid)
    rows = {str(r.ip): r for r in views.vlan_detail(conn, vid).devices}
    assert rows["10.1.40.16"].category == "CCTV"


def test_inherited_category_is_not_unknown(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "srv", "10.1.40.8", "10.1.40.8", "Server", "")
    row = next(r for r in views.vlan_detail(conn, vid).devices if str(r.ip) == "10.1.40.8")
    assert row.category == "Server" and row.inherited == "Server"
    bare = models.DeviceRow(
        row.device.__class__(**{**row.device.__dict__, "name": None}), row.ip, "srv"
    )
    assert bare.unknown  # no name, no own category, nothing inherited
    inheriting = models.DeviceRow(bare.device, row.ip, "srv", "Server")
    assert not inheriting.unknown


def test_zoom_page_renders(client, conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "camera", "")
    rid = store.list_ranges(conn, vid)[0].id
    page = client.get(f"/vlan/{vid}?zoom={rid}").text
    assert 'class="crumbs"' in page and "Show whole subnet" in page
    cells = page.count('<span class="z">') + page.count('<span class="z on">')
    assert cells == 5 and 'data-ip="10.1.40.19"' in page and 'data-ip="10.1.40.16"' not in page
    assert "Zoomed" in page and "Devices · 3 of" in page
    whole = client.get(f"/vlan/{vid}").text
    assert f'href="/vlan/{vid}?zoom={rid}"' in whole and f'href="/vlan/{vid}?zoom=dhcp"' in whole
    assert 'name="size"' in whole and "data-calc" in whole and "/static/app.js" in whole
