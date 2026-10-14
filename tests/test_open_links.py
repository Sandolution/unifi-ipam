from __future__ import annotations

from app import store


def test_ip_opens_in_a_new_tab(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    page = client.get(f"/vlan/{vid}").text
    row = page.split('id="dev-b4220073dc5c"')[1].split("</tr>")[0]  # Printer (Brother), .104
    assert (
        '<a class="open" href="http://10.1.20.104/" target="_blank" rel="noopener noreferrer"'
        in row
    )


def test_edit_row_keeps_fields_not_links(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    edit = client.get("/device/b4:22:00:73:dc:5c/edit", params={"vlan_id": vid}).text
    assert 'class="open"' not in edit and 'name="fixed_ip"' in edit
