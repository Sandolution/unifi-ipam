from __future__ import annotations

from app import store


def test_device_key_is_one_marker_per_line(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    page = client.get(f"/vlan/{vid}").text
    key = page.split('aria-label="What the markers mean"')[1].split("</ul>")[0]
    assert key.count("<li>") == 8
    for text in (
        "Online, offline",
        "Tinted row",
        "Grey italic category",
        "Outside range",
        "Forget",
    ):
        assert text in key


def test_banners_are_plain_markup_for_the_script(client, conn):
    vid = next(v for v in store.list_vlans(conn) if v.name == "Devices").id
    page = client.get(f"/vlan/{vid}?saved=Printers&forgot=Old%20phone").text
    toasts = (
        page.split('<div class="toasts"')[1].split("<header")[0]
        if "<header" in page.split('<div class="toasts"')[1]
        else page.split('<div class="toasts"')[1]
    )
    assert '<div class="banner ok">Saved range' in toasts
    assert '<div class="banner ok">Forgot' in toasts
    assert page.index('<div class="toasts"') < page.index(
        '<div class="layout">'
    )  # floats, not in the panel
