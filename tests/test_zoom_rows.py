from __future__ import annotations

from app import store


def _setup(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    store.save_range(conn, vid, "cams", "10.1.40.16", "10.1.40.20", "camera", "")
    rid = store.list_ranges(conn, vid)[0].id
    return vid, rid


def test_rows_zoom_in_and_zoomed_row_resets(client, conn):
    vid, rid = _setup(client, conn)
    whole = client.get(f"/vlan/{vid}").text
    assert f'data-href="/vlan/{vid}?zoom={rid}"' in whole
    assert f'data-href="/vlan/{vid}?zoom=dhcp"' in whole
    zoomed = client.get(f"/vlan/{vid}?zoom={rid}").text
    row = zoomed.split(f'data-href="/vlan/{vid}"')[0].rsplit("<tr", 1)[1]
    assert "zoomed" in row  # the zoomed row points back to the whole subnet
    assert f'href="/vlan/{vid}" title="Show whole subnet">cams' in zoomed  # breadcrumb chip


def test_zoom_legend_and_rules_list(client, conn):
    vid, rid = _setup(client, conn)
    zoomed = client.get(f"/vlan/{vid}?zoom={rid}").text
    assert "Free, click to copy" in zoomed and "Reserved, free" not in zoomed
    whole = client.get(f"/vlan/{vid}").text
    rules = whole.split('aria-label="How ranges work"')[1].split("</ul>")[0]
    assert rules.count("<li>") == 6 and "10.1.40.1–10.1.40.254" in rules
