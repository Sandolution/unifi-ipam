from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "anonymize_fixtures.py"
spec = importlib.util.spec_from_file_location("anonymize_fixtures", SCRIPT)
anon_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anon_mod)


def _run(tmp_path, monkeypatch, files: dict, *args: str) -> dict:
    for name, payload in files.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["anonymize", str(tmp_path), *args])
    assert anon_mod.main() == 0
    return {n: json.loads((tmp_path / n).read_text(encoding="utf-8")) for n in files}


def test_names_wifi_public_address_and_secrets(tmp_path, monkeypatch):
    payload = {
        "meta": {"rc": "ok"},
        "data": [
            {
                "name": "Laptop Alice",
                "hostname": "alice-pc.mydomain.com",
                "essid": "AliceNet",
                "wan_ip": "86.1.2.3",
                "dns": "9.9.9.9",
                "lan": "10.1.20.5",
                "x_passphrase": "hunter2",
                "wireguard_public_key": "abc=",
                "anon_client_id": "42",
            }
        ],
    }
    out = _run(
        tmp_path,
        monkeypatch,
        {"legacy_rest_user.json": payload},
        "--replace",
        "Alice=Alex",
        "--replace",
        "alice=alex",
        "--replace",
        "mydomain.com=example.com",
    )["legacy_rest_user.json"]["data"][0]
    assert out["name"] == "Laptop Alex" and out["hostname"] == "alex-pc.example.com"
    assert out["essid"] == "HomeWiFi"
    assert out["wan_ip"].startswith("203.0.113.") and out["dns"] == "9.9.9.9"
    assert out["lan"] == "10.1.20.5"  # private addresses stay
    for gone in ("x_passphrase", "wireguard_public_key", "anon_client_id"):
        assert gone not in out


def test_macs_keep_maker_prefix_and_private_bit_consistently(tmp_path, monkeypatch):
    a = {
        "meta": {"rc": "ok"},
        "data": [{"mac": "28:40:dd:13:d3:08", "ap_mac": "72:0a:b1:82:73:a5"}],
    }
    b = {"meta": {"rc": "ok"}, "data": [{"mac": "28:40:dd:13:d3:08"}]}
    out = _run(tmp_path, monkeypatch, {"a.json": a, "b.json": b})
    hw, private = out["a.json"]["data"][0]["mac"], out["a.json"]["data"][0]["ap_mac"]
    assert hw.startswith("28:40:dd:") and hw != "28:40:dd:13:d3:08"
    assert private.startswith("72:") and private != "72:0a:b1:82:73:a5"
    assert out["b.json"]["data"][0]["mac"] == hw  # same MAC, same replacement everywhere


def test_device_list_is_trimmed(tmp_path, monkeypatch):
    dev = {
        "meta": {"rc": "ok"},
        "data": [
            {
                "mac": "74:83:c2:00:00:01",
                "name": "Switch",
                "model": "US48PRO",
                "type": "usw",
                "port_table": [{"big": "config"}],
                "config_network": {"type": "static", "ip": "10.1.10.24", "x_key": 1},
            }
        ],
    }
    out = _run(tmp_path, monkeypatch, {"legacy_stat_device.json": dev})["legacy_stat_device.json"][
        "data"
    ][0]
    assert "port_table" not in out and out["config_network"] == {
        "type": "static",
        "ip": "10.1.10.24",
    }
