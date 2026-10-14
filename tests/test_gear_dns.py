from __future__ import annotations

import json

import httpx
import pytest

from app import store, sync
from app.unifi_adapter import (
    DnsRecord,
    UnifiAdapter,
    UnifiError,
    name_gear,
    parse_dns_records,
    parse_gear,
)
from tests.conftest import load

SWITCH = "74:83:c2:2f:1b:86"  # USW-Pro-48-PoE, 10.1.10.24
AP = "f4:e2:c6:6a:a9:51"  # U6+ (Living Room), 10.1.10.32


def _core(conn):
    return next(v for v in store.list_vlans(conn) if v.name == "Core")


def test_records_parsed_and_matched_to_gear_by_address():
    payload = [
        {
            "_id": "r1",
            "key": "switch.home.lan",
            "record_type": "A",
            "value": "10.1.10.24",
            "enabled": True,
        },
        {
            "_id": "r2",
            "key": "off.home.lan",
            "record_type": "A",
            "value": "10.1.10.25",
            "enabled": False,
        },
        {
            "_id": "r3",
            "key": "x",
            "record_type": "CNAME",
            "value": "switch.home.lan",
            "enabled": True,
        },
    ]
    records = parse_dns_records(payload)
    assert [r.id for r in records] == ["r1", "r2"]
    named = {g.mac: g for g in name_gear(parse_gear(load("legacy_stat_device.json")), records)}
    assert (named[SWITCH].dns_name, named[SWITCH].dns_record_id) == ("switch.home.lan", "r1")
    assert named["d0:21:f9:25:bb:08"].dns_name is None  # its record is disabled
    with pytest.raises(UnifiError):
        parse_dns_records({"meta": {"rc": "error"}})


def test_adapter_dns_calls():
    seen = []

    def handler(req):
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body))
        if body and body.get("key") == "bad name":
            return httpx.Response(400, json={"message": "Invalid Hostname", "errorCode": 400})
        return httpx.Response(200, json={"_id": "new1", **(body or {})})

    a = UnifiAdapter("https://udm.test", "k", verify_tls=False)
    a._client = httpx.Client(base_url="https://udm.test", transport=httpx.MockTransport(handler))
    assert a.create_dns_record("switch.home.lan", "10.1.10.24") == "new1"
    a.update_dns_record("new1", "sw.home.lan", "10.1.10.24")
    a.delete_dns_record("new1")
    base = "/proxy/network/v2/api/site/default/static-dns"
    assert seen == [
        (
            "POST",
            base,
            {"key": "switch.home.lan", "record_type": "A", "value": "10.1.10.24", "enabled": True},
        ),
        (
            "PUT",
            f"{base}/new1",
            {
                "_id": "new1",
                "key": "sw.home.lan",
                "record_type": "A",
                "value": "10.1.10.24",
                "enabled": True,
            },
        ),
        ("DELETE", f"{base}/new1", None),
    ]
    with pytest.raises(UnifiError, match="Invalid Hostname"):
        a.create_dns_record("bad name", "10.1.10.24")


def test_sync_stores_gear_dns_names(conn, monkeypatch):
    class Fake:
        def info(self):
            return type("I", (), {"version": "10.6.106"})()

        def networks(self):
            return []

        def clients(self):
            return []

        def gear(self):
            return parse_gear(load("legacy_stat_device.json"))

        def dns_records(self):
            return [DnsRecord("r1", "switch.home.lan", "10.1.10.24", True)]

    sync.run_sync(conn, Fake())
    d = store.get_device(conn, SWITCH)
    assert (d.dns_name, d.dns_record_id) == ("switch.home.lan", "r1")


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in ("create_dns_record", "update_dns_record", "delete_dns_record"):
        monkeypatch.setattr(
            UnifiAdapter, name, lambda self, *a, _n=name: seen.append((_n, *a)) or "new1"
        )
    monkeypatch.setattr(
        UnifiAdapter, "update_client", lambda *a, **k: seen.append(("update_client",))
    )
    monkeypatch.setattr("app.main.sync_once", lambda *a, **k: None)
    return seen


def _save(client, conn, mac=SWITCH, **fields):
    data = {"vlan_id": _core(conn).id, "category": "", "dns_name": ""}
    data.update(fields)
    return client.post(f"/device/{mac}", data=data)


def test_new_name_creates_a_record(client, conn, calls):
    r = _save(client, conn, dns_name="Switch.Home.LAN.")
    assert calls == [("create_dns_record", "switch.home.lan", "10.1.10.24")]
    assert "Saved as a custom DNS record in UniFi." in r.text
    assert "resolves to 10.1.10.24 within about 10 seconds" in r.text


def test_changed_name_updates_and_empty_deletes(client, conn, calls):
    with conn:
        conn.execute(
            "UPDATE device SET dns_name='switch.home.lan', dns_record_id='r1' WHERE mac=?",
            (SWITCH,),
        )
    _save(client, conn, dns_name="sw48.home.lan")
    _save(client, conn, dns_name="")
    assert calls == [
        ("update_dns_record", "r1", "sw48.home.lan", "10.1.10.24"),
        ("delete_dns_record", "r1"),
    ]


def test_category_only_never_touches_unifi(client, conn, calls):
    r = _save(client, conn, category="Network")
    assert calls == [] and store.get_device(conn, SWITCH).category == "Network"
    assert 'name="dns_name"' not in r.text


def test_duplicate_and_invalid_names_write_nothing(client, conn, calls):
    with conn:
        conn.execute(
            "UPDATE device SET dns_name='ap.home.lan', dns_record_id='r9' WHERE mac=?", (AP,)
        )
    dup = _save(client, conn, dns_name="ap.home.lan")
    bad = _save(client, conn, dns_name="bad name")
    assert calls == []
    assert "already used by U6+ (Living Room)" in dup.text and "not a valid DNS name" in bad.text
    assert 'name="dns_name"' in bad.text  # stays in edit mode


def test_unifi_refusal_stays_in_edit_mode(client, conn, monkeypatch):
    def boom(self, *a):
        raise UnifiError("POST /static-dns: Invalid Hostname")

    monkeypatch.setattr(UnifiAdapter, "create_dns_record", boom)
    r = _save(client, conn, dns_name="switch.home.lan")
    assert "UniFi refused" in r.text and 'name="dns_name"' in r.text


def test_gear_dns_name_is_marked_custom_dns(client, conn):
    with conn:
        conn.execute(
            "UPDATE device SET dns_name='switch.home.lan', dns_record_id='r1' WHERE mac=?",
            (SWITCH,),
        )
        conn.execute("UPDATE device SET dns_name='printer.lan' WHERE mac='b4:22:00:73:dc:5c'")
    core = client.get(f"/vlan/{_core(conn).id}").text
    row = core.split('id="dev-7483c22f1b86"')[1].split("</tr>")[0]
    assert "switch.home.lan</a>" in row and ">custom DNS</span>" in row
    devices = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    client_row = (
        client.get(f"/vlan/{devices}").text.split('id="dev-b4220073dc5c"')[1].split("</tr>")[0]
    )
    assert "printer.lan</a>" in client_row and "custom DNS" not in client_row  # per-device setting
