from __future__ import annotations

from app import store


def _vlan_id(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def test_index_redirects_to_first_vlan(client, conn):
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == f"/vlan/{_vlan_id(conn, 'Default')}"


def test_vlan_page_has_rail_and_strip(client, conn):
    r = client.get(f"/vlan/{_vlan_id(conn, 'Core')}")
    assert r.status_code == 200
    for name in ("Default", "Core", "Devices", "IoT", "Video", "DMZ", "Management"):
        assert name in r.text
    assert "No successful sync yet" in r.text
    assert r.text.count('class="c ') == 254
    assert "UniFi IPAM" in r.text


def test_vlan_page_and_404(client, conn):
    r = client.get(f"/vlan/{_vlan_id(conn, 'Video')}")
    assert r.status_code == 200
    assert "Camera | Garden" in r.text and "10.1.40.17" in r.text
    assert client.get("/vlan/9999").status_code == 404


def test_range_crud_via_forms(client, conn):
    vid = _vlan_id(conn, "Video")
    r = client.post(
        f"/vlan/{vid}/ranges",
        data=dict(name="cameras", start_ip="10.1.40.21", end_ip="10.1.40.60", category="camera"),
    )
    assert r.status_code == 303 and r.headers["location"].startswith(f"/vlan/{vid}?saved=")
    r = client.post(
        f"/vlan/{vid}/ranges", data=dict(name="clash", start_ip="10.1.40.50", end_ip="10.1.40.70")
    )
    assert r.status_code == 200 and "Overlaps range" in r.text  # re-rendered with error
    rid = store.list_ranges(conn, vid)[0].id
    assert client.get(f"/vlan/{vid}?edit={rid}").status_code == 200
    r = client.post(
        f"/ranges/{rid}",
        data=dict(name="cameras", start_ip="10.1.40.21", end_ip="10.1.40.61", category="camera"),
    )
    assert r.status_code == 303
    assert str(store.get_range(conn, rid).end) == "10.1.40.61"
    r = client.post(
        f"/vlan/{vid}/ranges", data=dict(name="pool", start_ip="10.1.40.230", end_ip="10.1.40.240")
    )
    assert r.status_code == 303 and "warn=" in r.headers["location"]
    assert client.post(f"/ranges/{rid}/delete").status_code == 303
    assert client.post("/ranges/9999/delete").status_code == 404
    assert [r.name for r in store.list_ranges(conn, vid)] == ["pool"]


def test_next_free_json_and_htmx(client, conn):
    vid = _vlan_id(conn, "Video")
    client.post(
        f"/vlan/{vid}/ranges", data=dict(name="cams", start_ip="10.1.40.16", end_ip="10.1.40.20")
    )
    r = client.get(f"/vlan/{vid}/next-free", params={"range": "cams"})
    assert r.json() == {"vlan_id": vid, "range": "cams", "ip": "10.1.40.19"}
    assert client.get(f"/vlan/{vid}/next-free", params={"range": "nope"}).status_code == 404


def test_device_update_returns_row(client, conn):
    vid = _vlan_id(conn, "Devices")
    r = client.post(
        "/device/b4:22:00:73:dc:5c",
        data=dict(vlan_id=vid, category="printer"),
    )
    assert r.status_code == 200
    assert ">printer<" in r.text and "<tr" in r.text
    assert "/edit?vlan_id=" in r.text  # back to the read-only row with an Edit button
    assert client.post("/device/ff:ff:ff:ff:ff:ff", data=dict(vlan_id=vid)).status_code == 404


def test_range_accepts_bare_host_octet(client, conn):
    vid = _vlan_id(conn, "Video")
    r = client.post(f"/vlan/{vid}/ranges", data=dict(name="cams", start_ip="21", end_ip="60"))
    assert r.status_code == 303
    rng = store.list_ranges(conn, vid)[0]
    assert (str(rng.start), str(rng.end)) == ("10.1.40.21", "10.1.40.60")
    page = client.get(f"/vlan/{vid}?edit={rng.id}").text
    assert 'class="prefix mono">10.1.40.</span>' in page and 'value="21"' in page
    assert 'id="rf" method="post" action="/ranges/' in page and page.count('form="rf"') == 7
    r = client.post(f"/ranges/{rng.id}", data=dict(name="cams", start_ip="10.1.40.21", end_ip="70"))
    assert r.status_code == 303 and str(store.get_range(conn, rng.id).end) == "10.1.40.70"


def test_theme_switch_present(client, conn):
    page = client.get(f"/vlan/{_vlan_id(conn, 'Core')}").text
    assert page.count('type="radio" name="theme"') == 3 and 'localStorage.getItem("theme")' in page


def test_health(client):
    assert client.get("/health").json()["ok"] is True
