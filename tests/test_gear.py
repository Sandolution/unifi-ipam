from __future__ import annotations

from ipaddress import IPv4Address as A

from app import checks, store, views
from app.unifi_adapter import parse_gear
from tests.conftest import load

SWITCH = "74:83:c2:2f:1b:86"  # USW-Pro-48-PoE, static 10.1.10.24


def _core(conn):
    return next(v for v in store.list_vlans(conn) if v.name == "Core")


def test_gear_is_parsed_with_static_address_as_fixed():
    gear = {g.name: g for g in parse_gear(load("legacy_stat_device.json"))}
    sw = gear["USW-Pro-48-PoE"]
    assert (sw.mac, sw.ip, sw.fixed_ip, sw.online, sw.gear_model) == (
        SWITCH,
        "10.1.10.24",
        "10.1.10.24",
        True,
        "Switch · US48PRO",
    )
    assert gear["UDM Pro"].fixed_ip is None  # internet side, DHCP
    assert all(g.user_id is None for g in gear.values())  # not clients


def test_gear_shows_in_core_and_counts_as_used(conn):
    view = views.vlan_detail(conn, _core(conn).id)
    shown = {str(r.ip): r.device.display_name for r in view.devices}
    for ip, name in {
        "10.1.10.24": "USW-Pro-48-PoE",
        "10.1.10.25": "USW Flex Mini",
        "10.1.10.32": "U6+ (Living Room)",
        "10.1.10.33": "U6+ (Study Room)",
    }.items():
        assert shown[ip] == name
    kinds = {str(c.ip): c.kind for c in view.cells}
    assert kinds["10.1.10.24"] != "free" and kinds["10.1.10.33"] != "free"
    assert "UDM Pro" not in shown.values()  # its internet address is in no VLAN


def test_next_free_and_conflicts_know_the_gear(conn):
    vid = _core(conn).id
    store.save_range(conn, vid, "infra", "10.1.10.24", "10.1.10.26", "", "")
    assert views.next_free(conn, vid, "infra") == A("10.1.10.26")
    errors, _ = store.check_fixed_ip(conn, _core(conn), "aa:aa:aa:00:00:01", A("10.1.10.32"))
    assert errors and "U6+ (Living Room)" in errors[0]


def test_gear_edits_dns_and_category_only_and_has_no_forget(client, conn):
    page = client.get(f"/vlan/{_core(conn).id}").text
    row = page.split('id="dev-7483c22f1b86"')[1].split("</tr>")[0]
    assert '<span class="badge badge-unifi"' in row and ">UniFi</span>" in row
    assert "UniFi gear" not in row and "/edit" in row and "/forget" not in row
    edit = client.get(f"/device/{SWITCH}/edit", params={"vlan_id": _core(conn).id}).text
    for field in ("dns_name", "category"):
        assert f'name="{field}"' in edit
    for field in ("name", "fixed_ip", "note", "unifi_fields"):
        assert f'name="{field}"' not in edit
    assert client.post(f"/device/{SWITCH}/forget").status_code == 400


def test_offline_gear_stale_check_offers_ignore_not_forget(client, conn):
    with conn:
        conn.execute("UPDATE device SET online = 0, last_seen = 1 WHERE mac = ?", (SWITCH,))
    report = checks.run_checks(conn, 14)
    assert any(f.device.mac == SWITCH for f in report.stale_fixed)
    page = client.get("/checks").text
    stale = page.split('id="stale"')[1].split("</section>")[0]
    row = stale.split("USW-Pro-48-PoE")[1].split("</tr>")[0]
    assert ">UniFi</span>" in row and "/forget" not in row and "Ignore" in row


def test_gear_type_is_the_note_in_grey_italics(client, conn):
    page = client.get(f"/vlan/{_core(conn).id}").text
    row = page.split('id="dev-7483c22f1b86"')[1].split("</tr>")[0]
    assert (
        '<span class="auto" title="Filled in automatically from UniFi">Switch · US48PRO</span>'
        in row
    )
    ap = page.split('id="dev-f4e2c66aa951"')[1].split("</tr>")[0]
    assert ">Access point · UAPL6</span>" in ap
