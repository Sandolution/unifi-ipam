from __future__ import annotations

from app import oui, store


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def test_bundled_registry_resolves_known_prefixes():
    assert oui.lookup("28:40:dd:5f:29:d3") == "Sony Interactive Entertainment Inc."
    assert oui.lookup("14:2B:2F:0F:9A:CC") == "Espressif Inc."
    assert oui.lookup("e4-ae-e4-c4-cf-17") == "Tuya Smart Inc."


def test_private_macs_get_no_vendor():
    for mac in ("72:f4:d6:d4:a0:36", "0a:49:4f:bd:5e:a5", "4e:5e:a1:5c:f3:c2", "be:4b:e3:94:6e:c5"):
        assert oui.is_private(mac) and oui.lookup(mac) is None
    assert not oui.is_private("84:1b:8c:43:08:80")


def test_unifi_vendor_wins_and_registry_fills_gaps(conn):
    with conn:
        conn.execute("UPDATE device SET vendor = NULL WHERE mac = 'b4:22:00:73:dc:5c'")
        conn.execute(
            "INSERT OR IGNORE INTO device (mac) VALUES ('28:40:dd:5f:29:d3'), ('72:f4:d6:d4:a0:36')"
        )
        conn.execute(
            "UPDATE device SET vendor = NULL"
            " WHERE mac IN ('28:40:dd:5f:29:d3', '72:f4:d6:d4:a0:36')"
        )
    assert store.get_device(conn, "b4:22:00:73:dc:5c").vendor == "Brother Industries, LTD."
    assert store.get_device(conn, "28:40:dd:5f:29:d3").vendor.startswith("Sony")
    phone = store.get_device(conn, "72:f4:d6:d4:a0:36")
    assert phone.vendor is None and phone.private_mac


def test_vendor_cell_shows_private_mac_label(client, conn):
    with conn:
        conn.execute("INSERT OR IGNORE INTO device (mac) VALUES ('72:f4:d6:d4:a0:36')")
        conn.execute(
            "UPDATE device SET vendor = NULL, vlan_unifi_id = '657f60b5232df83f9267f86b',"
            " last_ip = '10.1.20.235' WHERE mac = '72:f4:d6:d4:a0:36'"
        )
    page = client.get(f"/vlan/{_vid(conn, 'Devices')}").text
    row = page.split('id="dev-72f4d6d4a036"')[1].split("</tr>")[0]
    assert "Private MAC" in row


def test_slow_actions_are_marked_busy(client, conn):
    vid = _vid(conn, "Devices")
    store.save_range(conn, vid, "x", "10.1.20.200", "10.1.20.201", "", "")
    page = client.get(f"/vlan/{vid}").text
    assert 'data-busy="Forgetting…"' in page and 'data-busy="Deleting…"' in page
    assert 'data-busy="Adding…"' in page and 'class="busybar"' in page
    checks = client.get("/checks").text
    assert checks.count('data-busy="Forgetting…"') >= 2
