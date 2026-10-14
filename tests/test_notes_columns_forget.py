from __future__ import annotations

import sqlite3

from app import store
from app.db import connect, init_db
from app.unifi_adapter import UnifiAdapter, UnifiError


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def _old_db(path: str, column: str, value: str) -> None:
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE device (mac TEXT PRIMARY KEY, name TEXT, hostname TEXT, vendor TEXT,"
        f" category TEXT NOT NULL DEFAULT '', {column} TEXT NOT NULL DEFAULT '',"
        " first_seen INTEGER, last_seen INTEGER, last_ip TEXT, fixed_ip TEXT, unifi_note TEXT,"
        " vlan_unifi_id TEXT, online INTEGER NOT NULL DEFAULT 0);"
        f"INSERT INTO device (mac, {column}) VALUES ('aa:bb:cc:00:00:01', '{value}');"
    )
    old.commit()
    old.close()


def test_migration_drops_empty_app_note_and_adds_user_id(tmp_path):
    path = str(tmp_path / "old.db")
    _old_db(path, "note", "")
    init_db(path)
    init_db(path)  # idempotent
    conn = connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(device)")}
    assert "note" not in cols and "unifi_user_id" in cols
    assert store.get_device(conn, "aa:bb:cc:00:00:01") is not None
    conn.close()


def test_migration_keeps_old_text_rather_than_losing_it(tmp_path):
    path = str(tmp_path / "old.db")
    _old_db(path, "tags", "garden, poe")
    init_db(path)
    conn = connect(path)
    assert "tags" in {r[1] for r in conn.execute("PRAGMA table_info(device)")}
    assert conn.execute("SELECT tags FROM device").fetchone()[0] == "garden, poe"
    conn.close()


def test_vlan_page_has_column_picker_and_forget_on_offline_only(client, conn):
    vid = _vid(conn, "Devices")
    page = client.get(f"/vlan/{vid}").text
    assert 'data-cols-for="devices"' in page and 'id="devices"' in page
    for key in ("mac", "vendor", "range", "category", "note", "seen"):
        assert f'value="{key}"' in page and f"col-{key}" in page
    rows = page.split('<tr id="dev-')[1:]
    online = [r for r in rows if " online" in r.split(">", 1)[0]]
    offline = [r for r in rows if " offline" in r.split(">", 1)[0]]
    assert online and offline
    assert all("/forget" not in r.split("</tr>")[0] for r in online)
    assert all("/forget" in r.split("</tr>")[0] for r in offline)
    assert f'name="next" value="/vlan/{vid}"' in page


def test_forget_returns_to_next_page_with_banner(client, conn, monkeypatch):
    monkeypatch.setattr(UnifiAdapter, "forget_clients", lambda self, macs: None)
    vid = _vid(conn, "Devices")
    mac = "b4:22:00:73:dc:5c"
    r = client.post(f"/device/{mac}/forget", data={"next": f"/vlan/{vid}?zoom=dhcp"})
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/vlan/{vid}?zoom=dhcp&forgot=")
    assert "Forgot" in client.get(r.headers["location"]).text
    assert store.get_device(conn, mac) is None


def test_forget_rejects_offsite_next(client, conn, monkeypatch):
    monkeypatch.setattr(UnifiAdapter, "forget_clients", lambda self, macs: None)
    for bad in ("https://evil.example/", "//evil.example/x"):
        mac = next(d.mac for d in store.list_devices(conn) if not d.online)
        r = client.post(f"/device/{mac}/forget", data={"next": bad})
        assert r.headers["location"].startswith("/checks?forgot=")


def test_forget_error_goes_back_to_vlan(client, conn, monkeypatch):
    def boom(self, macs):
        raise UnifiError("POST /cmd/stamgr: api.err.Invalid")

    monkeypatch.setattr(UnifiAdapter, "forget_clients", boom)
    vid = _vid(conn, "Devices")
    r = client.post("/device/b4:22:00:73:dc:5c/forget", data={"next": f"/vlan/{vid}"})
    assert r.headers["location"].startswith(f"/vlan/{vid}?err=")
    assert "UniFi refused" in client.get(r.headers["location"]).text


def test_duplicates_have_forget_buttons(client, conn):
    page = client.get("/checks").text
    dup = page.split('id="dup"')[1].split("</section>")[0]
    assert 'name="next" value="/checks#dup"' in dup and "Forget in UniFi" in dup


def test_brand_is_unifi_ipam(client, conn):
    page = client.get(f"/vlan/{_vid(conn, 'Core')}").text
    assert "UniFi <b>IPAM</b>" in page and "Unify" not in page
