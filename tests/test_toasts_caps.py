from __future__ import annotations

from app import store


def test_validation_errors_are_capitalised_and_float(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    r = client.post(f"/vlan/{vid}/ranges", data={"name": "x", "start_ip": "50", "end_ip": "40"})
    assert r.status_code == 200
    toasts = r.text.split('<div class="toasts"')[1].split('</div>\n<div class="layout">')[0]
    assert '<div class="banner bad">Start must be &lt;= end.</div>' in toasts


def test_dhcp_overlap_warning_is_capitalised(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    r = client.post(f"/vlan/{vid}/ranges", data={"name": "p", "start_ip": "230", "end_ip": "240"})
    page = client.get(r.headers["location"]).text
    assert '<div class="banner warn">Overlaps the DHCP pool' in page


def test_range_rows_carry_subnet_bounds_and_no_autofill(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "IoT").id
    page = client.get(f"/vlan/{vid}").text
    assert 'data-first="10.1.30.1" data-last="10.1.30.254"' in page
    assert 'name="end_ip" required aria-label="End" autocomplete="off"' in page
