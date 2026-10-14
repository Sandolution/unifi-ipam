from __future__ import annotations

from ipaddress import IPv4Address as A

from app import store, views


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def test_range_stats_count_like_the_strip(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "low", "10.1.40.1", "10.1.40.20", "", "")  # gateway + 4 devices
    view = views.vlan_detail(conn, vid)
    st = view.stats[store.list_ranges(conn, vid)[0].id]
    assert (st.used, st.free) == (5, 15)  # .1 gw, .8, .16, .17, .18
    assert st.next_free == A("10.1.40.2")
    assert view.dhcp_stats.used + view.dhcp_stats.free == view.dhcp_size


def test_full_range_has_no_next_free(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.18", "", "")
    st = views.vlan_detail(conn, vid).stats[store.list_ranges(conn, vid)[0].id]
    assert (st.used, st.free, st.next_free) == (3, 0, None)


def test_ranges_table_shows_next_free_without_button(client, conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "", "")
    store.save_range(conn, vid, "full", "10.1.40.8", "10.1.40.8", "", "")
    page = client.get(f"/vlan/{vid}").text
    assert ">Find<" not in page and "next-free?range" not in page
    assert 'class="copyable" data-ip="10.1.40.19"' in page
    assert "Used / Free" in page and ">Full<" in page


def test_zoom_tooltips_name_devices_not_ranges(conn):
    vid = _vid(conn, "Video")
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "Camera", "")
    rid = store.list_ranges(conn, vid)[0].id
    titles = {str(c.ip): c.title for c in views.vlan_detail(conn, vid, str(rid)).zoom_cells}
    garden = titles["10.1.40.17"].split("\n")
    assert garden[0] == "10.1.40.17"
    assert garden[1].startswith("Camera | Garden · e4:24:6c:bd:9c:67")
    assert "cams" not in titles["10.1.40.17"]
    assert titles["10.1.40.19"] == "10.1.40.19\nFree · click to copy"


def test_zoom_tooltip_lists_every_device_on_a_shared_ip(conn):
    from app.sync import write_clients
    from app.unifi_adapter import UnifiClient

    vid = _vid(conn, "Video")
    ghost = UnifiClient(
        "00:00:00:00:00:99", "Old cam", None, None, "10.1.40.17", None, None, None, 1, 2, False
    )
    with conn:
        write_clients(conn, [ghost])
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "", "")
    rid = store.list_ranges(conn, vid)[0].id
    cell = next(
        c for c in views.vlan_detail(conn, vid, str(rid)).zoom_cells if str(c.ip) == "10.1.40.17"
    )
    lines = cell.title.split("\n")
    assert len(lines) == 3 and any("Old cam" in x and "Offline" in x for x in lines)
