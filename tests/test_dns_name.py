from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from app import store
from app.db import connect, init_db
from app.unifi_adapter import UnifiAdapter, parse_clients

MAC = "b4:22:00:73:dc:5c"  # Printer (Brother), fixed 10.1.20.104
DYNAMIC = "68:4a:e9:d4:e7:2e"  # fixed .16; used as a second device


def _env(rows):
    return {"meta": {"rc": "ok"}, "data": rows}


def test_dns_name_is_read_only_when_enabled():
    users = _env(
        [
            {
                "_id": "1",
                "mac": "aa:aa:aa:00:00:01",
                "local_dns_record_enabled": True,
                "local_dns_record": "nas.lan",
            },
            {
                "_id": "2",
                "mac": "aa:aa:aa:00:00:02",
                "local_dns_record_enabled": False,
                "local_dns_record": "old.lan",
            },
        ]
    )
    c = {x.mac: x for x in parse_clients(users, _env([]))}
    assert c["aa:aa:aa:00:00:01"].dns_name == "nas.lan"
    assert c["aa:aa:aa:00:00:02"].dns_name is None


def test_adapter_sends_local_dns_record():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})

    a = UnifiAdapter("https://udm.test", "k", verify_tls=False)
    a._client = httpx.Client(base_url="https://udm.test", transport=httpx.MockTransport(handler))
    a.update_client("u", dns_name="printer.lan")
    a.update_client("u", clear_dns_name=True)
    assert seen == [
        {"local_dns_record_enabled": True, "local_dns_record": "printer.lan"},
        {"local_dns_record_enabled": False, "local_dns_record": ""},
    ]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(UnifiAdapter, "update_client", lambda self, uid, **kw: seen.append(kw))
    monkeypatch.setattr("app.main.sync_once", lambda *a, **k: None)
    return seen


def _post(client, conn, mac=MAC, **fields):
    d = store.get_device(conn, mac)
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    data = {
        "unifi_fields": "1",
        "vlan_id": vid,
        "category": d.category,
        "name": d.name or "",
        "note": d.unifi_note or "",
        "fixed_ip": str(d.fixed_ip or "").rsplit(".", 1)[-1],
        "dns_name": d.dns_name or "",
    }
    data.update(fields)
    return client.post(f"/device/{mac}", data=data)


def test_dns_name_goes_to_unifi_lowercased(client, conn, calls):
    _post(client, conn, dns_name="Printer.LAN.")
    assert calls == [{"dns_name": "printer.lan"}]


@pytest.mark.parametrize(
    "value, message",
    [
        ("printer lan", "not a valid DNS name"),
        ("-printer.lan", "not a valid DNS name"),
        ("a..lan", "not a valid DNS name"),
    ],
)
def test_invalid_dns_names_write_nothing(client, conn, calls, value, message):
    r = _post(client, conn, dns_name=value)
    assert calls == [] and message in r.text


def test_dns_name_must_be_unique(client, conn, calls):
    with conn:
        conn.execute("UPDATE device SET dns_name = 'printer.lan' WHERE mac = ?", (DYNAMIC,))
    r = _post(client, conn, dns_name="printer.lan")
    assert calls == [] and "already used by Smartphone Alex" in r.text


def test_dns_name_needs_a_fixed_ip(client, conn, calls):
    r = _post(client, conn, dns_name="printer.lan", fixed_ip="")
    assert calls == [] and "needs a fixed IP" in r.text


def test_clearing_dns_name(client, conn, calls):
    with conn:
        conn.execute("UPDATE device SET dns_name = 'printer.lan' WHERE mac = ?", (MAC,))
    _post(client, conn, dns_name="")
    assert calls == [{"clear_dns_name": True}]


def test_dns_column_shown_and_selectable(client, conn):
    with conn:
        conn.execute("UPDATE device SET dns_name = 'printer.lan' WHERE mac = ?", (MAC,))
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    page = client.get(f"/vlan/{vid}").text
    assert '<th class="col-dns"><span class="hold-dns">DNS</span></th>' in page
    assert 'value="dns"' in page
    assert 'href="http://printer.lan/" target="_blank" rel="noopener noreferrer"' in page
    edit = client.get(f"/device/{MAC}/edit", params={"vlan_id": vid}).text
    assert 'name="dns_name" value="printer.lan"' in edit


def test_migration_adds_dns_column(tmp_path):
    path = str(tmp_path / "x.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE device (mac TEXT PRIMARY KEY, name TEXT, hostname TEXT, vendor TEXT,"
        " category TEXT NOT NULL DEFAULT '', first_seen INTEGER, last_seen INTEGER,"
        " last_ip TEXT, fixed_ip TEXT, unifi_note TEXT, vlan_unifi_id TEXT,"
        " unifi_user_id TEXT, online INTEGER NOT NULL DEFAULT 0)"
    )
    old.commit()
    old.close()
    init_db(path)
    conn = connect(path)
    assert "dns_name" in {r[1] for r in conn.execute("PRAGMA table_info(device)")}
    conn.close()
