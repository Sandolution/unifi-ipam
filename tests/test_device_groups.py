from __future__ import annotations

from app import store, views


def test_devices_grouped_by_range_in_address_order(conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.17", "", "")
    groups = views.device_groups(views.vlan_detail(conn, vid))
    shape = [(g.range.name if g.range else g.kind, [str(r.ip) for r in g.rows]) for g in groups]
    assert shape == [
        ("none", ["10.1.40.8"]),
        ("cams", ["10.1.40.16", "10.1.40.17"]),
        ("none", ["10.1.40.18"]),
    ]
    assert groups[1].stats.used == 2 and groups[1].hue is not None


def test_group_rows_render_and_hide_when_zoomed(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.17", "", "")
    page = client.get(f"/vlan/{vid}").text
    assert page.count('<tr class="group') == 3 and "Not in a range" in page
    rid = store.list_ranges(conn, vid)[0].id
    assert '<tr class="group' not in client.get(f"/vlan/{vid}?zoom={rid}").text


def test_dhcp_pool_devices_get_their_own_group(client, conn):
    from app.sync import write_clients
    from app.unifi_adapter import UnifiClient

    with conn:
        write_clients(
            conn,
            [
                UnifiClient(
                    "00:00:00:00:00:31",
                    "Lease",
                    None,
                    None,
                    "10.1.40.233",
                    None,
                    None,
                    "6828dfac4a6b661c7d5bdf5d",
                    1,
                    2,
                    True,
                ),
            ],
        )
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    groups = views.device_groups(views.vlan_detail(conn, vid))
    dhcp = [g for g in groups if g.kind == "dhcp"]
    assert len(dhcp) == 1 and [r.device.name for r in dhcp[0].rows] == ["Lease"]
    assert str(dhcp[0].start) == "10.1.40.232" and dhcp[0].stats.used == 1
    page = client.get(f"/vlan/{vid}").text
    group_row = page.split('<tr class="group dhcp')[1].split("</tr>")[0]
    assert "DHCP pool" in group_row and ".232–.235" in group_row
    lease_row = page.split('id="dev-000000000031"')[1].split("</tr>")[0]
    assert '<span class="faint">DHCP pool</span>' in lease_row


def test_planned_range_over_the_pool_wins(conn):
    from app.sync import write_clients
    from app.unifi_adapter import UnifiClient

    with conn:
        write_clients(
            conn,
            [
                UnifiClient(
                    "00:00:00:00:00:31",
                    "Lease",
                    None,
                    None,
                    "10.1.40.233",
                    None,
                    None,
                    "6828dfac4a6b661c7d5bdf5d",
                    1,
                    2,
                    True,
                ),
            ],
        )
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    store.save_range(conn, vid, "tail", "10.1.40.230", "10.1.40.240", "", "")
    kinds = [
        (g.kind, g.range.name if g.range else None)
        for g in views.device_groups(views.vlan_detail(conn, vid))
    ]
    assert ("range", "tail") in kinds and all(k != "dhcp" for k, _ in kinds)
