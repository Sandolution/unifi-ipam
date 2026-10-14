from __future__ import annotations

from app import checks, store


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


MAC = "b4:22:00:73:dc:5c"  # Printer (Brother), Devices VLAN


def test_device_rows_are_read_only_until_edit(client, conn):
    vid = _vid(conn, "Devices")
    page = client.get(f"/vlan/{vid}").text
    row = page.split('id="dev-b4220073dc5c"')[1].split("</tr>")[0]
    assert 'name="category"' not in row and 'name="note"' not in row
    assert f"/device/{MAC}/edit?vlan_id={vid}" in row


def test_edit_row_save_and_cancel(client, conn):
    vid = _vid(conn, "Devices")
    edit = client.get(f"/device/{MAC}/edit", params={"vlan_id": vid, "zoom": "dhcp"}).text
    assert 'class="editing' in edit and 'name="category"' in edit and "autofocus" in edit
    assert 'name="zoom" value="dhcp"' in edit and "data-save" in edit and "data-cancel" in edit
    saved = client.post(
        f"/device/{MAC}", data={"vlan_id": vid, "category": "Printer", "zoom": "dhcp"}
    )
    assert ">Printer<" in saved.text and 'name="category"' not in saved.text
    assert store.get_device(conn, MAC).category == "Printer"
    view = client.get(f"/device/{MAC}/row", params={"vlan_id": vid}).text
    assert 'name="category"' not in view and ">Printer<" in view


def test_ignore_keeps_listed_but_not_counted(client, conn):
    report = checks.run_checks(conn, 14, now=1791718322)
    assert report.stale_fixed
    before = report.total
    f = report.stale_fixed[0]
    r = client.post(
        "/checks/ignore", data={"kind": "stale", "subject": f.device.mac, "next": "/checks#stale"}
    )
    assert r.status_code == 303 and r.headers["location"] == "/checks#stale"
    after = checks.run_checks(conn, 14, now=1791718322)
    assert len(after.stale_fixed) == len(report.stale_fixed)  # still listed
    assert after.total == before - 1  # not counted
    assert after.counts()["stale"][1] == 1
    page = client.get("/checks").text
    assert 'class="ignored"' in page and "Unignore" in page and "(1 ignored)" in page
    client.post("/checks/ignore", data={"kind": "stale", "subject": f.device.mac, "ignore": "0"})
    assert checks.run_checks(conn, 14, now=1791718322).total == before


def test_ignore_conflict_group_and_reject_unknown_kind(client, conn):
    from app.sync import write_clients
    from app.unifi_adapter import UnifiClient

    with conn:
        write_clients(
            conn,
            [
                UnifiClient(
                    "00:00:00:00:00:01",
                    "a",
                    None,
                    None,
                    "10.1.10.50",
                    "10.1.10.50",
                    None,
                    None,
                    1,
                    2,
                    False,
                ),
                UnifiClient(
                    "00:00:00:00:00:02", "b", None, None, "10.1.10.50", None, None, None, 1, 2, True
                ),
            ],
        )
    report = checks.run_checks(conn, 14)
    g = next(g for g in report.conflicts if str(g.ip) == "10.1.10.50")
    before = report.total
    client.post("/checks/ignore", data={"kind": "dup", "subject": g.subject})
    assert checks.run_checks(conn, 14).total == before - 1
    assert client.post("/checks/ignore", data={"kind": "nope", "subject": "x"}).status_code == 400
