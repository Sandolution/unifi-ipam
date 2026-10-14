from __future__ import annotations

import json

import httpx
import pytest

from app import store
from app.unifi_adapter import UnifiAdapter, UnifiError


def _adapter(handler) -> UnifiAdapter:
    a = UnifiAdapter("https://udm.test", "key", verify_tls=False)
    a._client = httpx.Client(
        base_url="https://udm.test",
        headers={"X-API-KEY": "key"},
        transport=httpx.MockTransport(handler),
    )
    return a


def test_forget_posts_forget_sta():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers.get("x-api-key")
        return httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})

    _adapter(handler).forget_clients(["AA:BB:CC:00:00:01"])
    assert seen["path"] == "/proxy/network/api/s/default/cmd/stamgr"
    assert seen["body"] == {"cmd": "forget-sta", "macs": ["aa:bb:cc:00:00:01"]}
    assert seen["key"] == "key"


def test_forget_raises_on_controller_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"meta": {"rc": "error", "msg": "api.error.NoMacSpecified"}}
        )

    with pytest.raises(UnifiError, match="NoMacSpecified"):
        _adapter(handler).forget_clients(["aa:bb:cc:00:00:01"])
    with pytest.raises(UnifiError):
        _adapter(handler).forget_clients([])


def test_forget_route_removes_locally_only_after_unifi_ok(client, conn, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(UnifiAdapter, "forget_clients", lambda self, macs: calls.append(macs))
    mac = "b4:22:00:73:dc:5c"
    r = client.post(f"/device/{mac}/forget", headers={"referer": "/checks#stale"})
    assert r.status_code == 303 and r.headers["location"].endswith("#stale")
    assert "forgot=Printer" in r.headers["location"]
    page = client.get(r.headers["location"].split("#")[0])
    assert "Forgot “Printer (Brother)” in UniFi" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert calls == [[mac]]
    assert store.get_device(conn, mac) is None
    assert client.post("/device/ff:ff:ff:ff:ff:ff/forget").status_code == 404


def test_forget_route_keeps_device_when_unifi_refuses(client, conn, monkeypatch):
    def boom(self, macs):
        raise UnifiError("POST /cmd/stamgr: api.err.Invalid")

    monkeypatch.setattr(UnifiAdapter, "forget_clients", boom)
    mac = "b4:22:00:73:dc:5c"
    r = client.post(f"/device/{mac}/forget")
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert store.get_device(conn, mac) is not None
    assert "UniFi refused" in client.get(r.headers["location"]).text
