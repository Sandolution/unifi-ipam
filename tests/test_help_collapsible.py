from __future__ import annotations

from app import store


def test_help_lists_are_collapsible_and_closed_by_default(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    page = client.get(f"/vlan/{vid}").text
    assert '<details class="help" data-remember="help-ranges">' in page
    assert '<details class="help" data-remember="help-devices">' in page
    assert "<summary>How ranges work</summary>" in page and "<summary>Legend</summary>" in page
    assert '<details class="help" open' not in page
