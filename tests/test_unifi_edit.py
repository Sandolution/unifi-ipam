from __future__ import annotations

import json

import httpx
import pytest

from app import store
from app.unifi_adapter import UnifiAdapter, UnifiError

MAC = "b4:22:00:73:dc:5c"  # Printer (Brother), fixed 10.1.20.104, Devices VLAN


def _adapter(seen: list) -> UnifiAdapter:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})

    a = UnifiAdapter("https://udm.test", "key", verify_tls=False)
    a._client = httpx.Client(base_url="https://udm.test", transport=httpx.MockTransport(handler))
    return a


def test_update_client_sends_only_what_is_given():
    seen: list = []
    a = _adapter(seen)
    a.update_client("u1", name="Printer", note="Hall")
    a.update_client("u1", fixed_ip="10.1.20.105", network_id="net20")
    a.update_client("u1", clear_fixed_ip=True)
    a.update_client("u1")  # nothing to send: no request
    base = "/proxy/network/api/s/default/rest/user/u1"
    assert seen == [
        ("PUT", base, {"name": "Printer", "note": "Hall", "noted": True}),
        ("PUT", base, {"use_fixedip": True, "fixed_ip": "10.1.20.105", "network_id": "net20"}),
        ("PUT", base, {"use_fixedip": False}),
    ]


def test_clearing_a_note_unsets_noted():
    seen: list = []
    _adapter(seen).update_client("u1", note="")
    assert seen[0][2] == {"note": "", "noted": False}


def test_user_ids_come_from_unifi(conn):
    assert store.get_device(conn, MAC).unifi_user_id
    assert store.get_device(conn, "68:4a:e9:d4:e7:2e").unifi_user_id == "657f6f13232df83f9267fa32"


@pytest.fixture
def calls(monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        UnifiAdapter, "update_client", lambda self, uid, **kw: seen.append((uid, kw))
    )
    monkeypatch.setattr("app.main.sync_once", lambda *a, **k: None)
    return seen


def _vid(conn):
    return next(v for v in store.list_vlans(conn) if v.name == "Devices").id


def _post(client, conn, **fields):
    d = store.get_device(conn, MAC)
    data = {
        "unifi_fields": "1",
        "vlan_id": _vid(conn),
        "category": d.category,
        "name": d.name or "",
        "note": d.unifi_note or "",
        "fixed_ip": "104",
    }
    data.update(fields)
    return client.post(f"/device/{MAC}", data=data)


def test_category_only_never_contacts_unifi(client, conn, calls):
    r = _post(client, conn, category="Printers")
    assert calls == [] and ">Printers<" in r.text and "Saved in UniFi" not in r.text


def test_name_and_note_go_to_unifi(client, conn, calls):
    r = _post(client, conn, name="Printer hall", note="Toner in cupboard")
    uid = store.get_device(conn, MAC).unifi_user_id
    assert calls == [(uid, {"name": "Printer hall", "note": "Toner in cupboard"})]
    assert 'hx-swap-oob="beforeend"' in r.text and "Saved in UniFi." in r.text
    assert 'name="name"' not in r.text  # back to the read-only row


def test_new_fixed_ip_is_checked_then_sent(client, conn, calls):
    vlan = store.get_vlan(conn, _vid(conn))
    r = _post(client, conn, fixed_ip="150")
    assert calls[-1][1] == {"fixed_ip": "10.1.20.150", "network_id": vlan.unifi_id}
    assert "renews its lease" in r.text


@pytest.mark.parametrize(
    "value, message",
    [
        ("1", "is the gateway"),
        ("255", "network or broadcast"),
        ("10.1.30.5", "is outside 10.1.20.0/24"),
        ("16", "is taken: Smartphone Alex"),
        ("abc", "is not an IPv4 address"),
    ],
)
def test_bad_fixed_ip_writes_nothing(client, conn, calls, value, message):
    r = _post(client, conn, fixed_ip=value, category="Changed", name="Changed")
    assert calls == []
    assert message in r.text and 'class="banner bad"' in r.text
    assert 'name="fixed_ip"' in r.text  # stays in edit mode
    assert store.get_device(conn, MAC).category != "Changed"


def test_dhcp_pool_reservation_is_a_warning(client, conn, calls):
    r = _post(client, conn, fixed_ip="233")
    assert calls and 'class="banner warn"' in r.text and "inside the DHCP pool" in r.text


def test_clearing_the_fixed_ip(client, conn, calls):
    _post(client, conn, fixed_ip="")
    assert calls[-1][1] == {"clear_fixed_ip": True}


def test_unifi_refusal_keeps_edit_row(client, conn, monkeypatch):
    def boom(self, uid, **kw):
        raise UnifiError("PUT /rest/user/x: api.err.Invalid")

    monkeypatch.setattr(UnifiAdapter, "update_client", boom)
    r = _post(client, conn, name="New name")
    assert "UniFi refused" in r.text and 'name="name"' in r.text


def test_edit_row_has_unifi_fields(client, conn):
    r = client.get(f"/device/{MAC}/edit", params={"vlan_id": _vid(conn)}).text
    assert 'name="fixed_ip" value="104" data-orig="104" data-prefix="10.1.20."' in r
    assert 'name="name" value="Printer (Brother)"' in r and 'name="note"' in r


def test_clearing_name_and_note(client, conn, calls):
    _post(client, conn, name="", note="")
    assert calls[-1][1] == {"name": ""}  # the printer has no UniFi note, so only the name changes
