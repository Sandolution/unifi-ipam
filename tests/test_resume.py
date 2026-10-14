from __future__ import annotations

from app import store


def test_only_a_reposted_form_blocks_the_refresh_on_return(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Video").id
    assert "data-no-renew" not in client.get(f"/vlan/{vid}").text
    bad = client.post(f"/vlan/{vid}/ranges", data={"name": "x", "start_ip": "50", "end_ip": "40"})
    assert bad.status_code == 200 and "<body data-no-renew>" in bad.text
    assert "data-no-renew" not in client.get("/checks").text
