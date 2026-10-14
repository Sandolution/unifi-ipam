"""Strip secrets from UniFi sample responses and anonymize them before they are committed.

Run this on the JSON files in tests/fixtures after refreshing them from a real controller:

    python -I scripts/anonymize_fixtures.py tests/fixtures \\
        --replace Alice=Alex --replace mydomain.com=example.com --wifi HomeWiFi \\
        --map-out mac-map.json

What it does, in every JSON value of every file:

- Secrets are removed: every key starting with "x_" (UniFi's convention for secret fields) and
  every key naming a password, private key, pre-shared key, secret, token or hash.
- legacy_stat_device.json is trimmed to the fields the app reads; the full response is large
  and full of device configuration. legacy_stat_sysinfo.json keeps only version fields.

- MAC addresses keep their first octet (so "private/randomised" detection still works) and, for
  hardware addresses, their maker prefix (so the vendor lookup still works). The rest is replaced
  using a keyed hash with a random key, so the originals cannot be worked out. The same MAC gets
  the same replacement in every file, so links between records survive.
- Wi-Fi network names (essid) become the --wifi value: a network name plus access point MACs
  can locate a home through public Wi-Fi maps.
- Public IPv4 addresses become addresses from 203.0.113.0/24 (reserved for documentation);
  well-known public DNS resolvers are kept.
- Unique ids and keys that identify a console or person are removed.
- Each --replace OLD=NEW is applied to every string (names, domains, serial-like hostnames).

--map-out writes the old -> new MAC mapping so tests that name a MAC can be updated; delete that
file afterwards, it undoes the MAC anonymization. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sys
from pathlib import Path

MAC = re.compile(r"^([0-9a-f]{2})([:-][0-9a-f]{2}){5}$", re.I)
DROP_KEYS = {"anon_client_id", "wan_username", "direct_connect_domain", "last_1x_identity"}
DROP_SUFFIXES = ("public_key", "_identity")
SECRET = re.compile(r"^x_|password|passphrase|private_key|psk|secret|token|api_key|hash", re.I)
TRIM = {
    "legacy_stat_device.json": (
        "_id",
        "mac",
        "ip",
        "name",
        "model",
        "type",
        "state",
        "adopted",
        "last_seen",
        "uptime",
        "config_network",
    ),
    "legacy_stat_sysinfo.json": (
        "version",
        "build",
        "previous_version",
        "timezone",
        "console_display_version",
        "udm_version",
        "ubnt_device_type",
    ),
}
NETWORK_KEEP = ("type", "ip", "netmask", "gateway")
KEEP_PUBLIC = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112"}


class Anonymizer:
    def __init__(self, replacements: list[tuple[str, str]], wifi: str, key: bytes) -> None:
        self.replacements = replacements
        self.wifi = wifi
        self.key = key
        self.macs: dict[str, str] = {}
        self.ips: dict[str, str] = {}

    def mac(self, value: str) -> str:
        old = value.lower().replace("-", ":")
        if old not in self.macs:
            octets = old.split(":")
            keep = 1 if int(octets[0], 16) & 0x02 else 3  # private address: keep only octet 1
            digest = hmac.new(self.key, old.encode(), hashlib.sha256).hexdigest()
            fresh = [digest[i : i + 2] for i in range(0, 12, 2)]
            self.macs[old] = ":".join(octets[:keep] + fresh[keep:])
        return self.macs[old]

    def ip(self, value: str) -> str:
        if value not in self.ips:
            self.ips[value] = f"203.0.113.{10 + len(self.ips)}"
        return self.ips[value]

    def text(self, value: str) -> str:
        if MAC.match(value):
            return self.mac(value)
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            addr = None
        if addr and addr.version == 4 and addr.is_global and value not in KEEP_PUBLIC:
            return self.ip(value)
        for old, new in self.replacements:
            value = value.replace(old, new)
        return value

    def walk(self, node, key: str = ""):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in DROP_KEYS or k.endswith(DROP_SUFFIXES) or SECRET.search(k):
                    continue
                out[k] = self.wifi if "essid" in k and isinstance(v, str) else self.walk(v, k)
            return out
        if isinstance(node, list):
            return [self.walk(v, key) for v in node]
        if isinstance(node, str):
            return self.text(node)
        return node


def trim(name: str, payload):
    """Keep only the fields the app reads, for the files that are large or noisy."""
    keep = TRIM.get(name)
    if not keep or not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return payload
    rows = []
    for row in payload["data"]:
        out = {k: row[k] for k in keep if k in row}
        if isinstance(out.get("config_network"), dict):
            out["config_network"] = {
                k: v for k, v in out["config_network"].items() if k in NETWORK_KEEP
            }
        rows.append(out)
    return {**payload, "data": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--replace", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--wifi", default="HomeWiFi")
    ap.add_argument("--map-out", type=Path, help="write the old -> new MAC mapping here")
    args = ap.parse_args()
    pairs = []
    for item in args.replace:
        old, sep, new = item.partition("=")
        if not sep or not old:
            print(f"--replace needs OLD=NEW, got {item!r}", file=sys.stderr)
            return 2
        pairs.append((old, new))
    pairs.sort(key=lambda p: -len(p[0]))  # longest first, so "Alexandra" wins over "Alex"
    anon = Anonymizer(pairs, args.wifi, secrets.token_bytes(32))
    files = sorted(args.folder.glob("*.json"))
    for f in files:
        data = trim(f.name, json.loads(f.read_text(encoding="utf-8")))
        data = anon.walk(data)
        f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.map_out:
        args.map_out.write_text(json.dumps(anon.macs, indent=1) + "\n", encoding="utf-8")
    print(f"{len(files)} files, {len(anon.macs)} MACs, {len(anon.ips)} public addresses replaced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
