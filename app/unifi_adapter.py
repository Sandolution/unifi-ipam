"""The only module that talks to UniFi. Everything else sees the dataclasses below.

Uses the legacy Network API through the UDM proxy (see docs/unifi-api-findings.md).
Parsing is split from transport so tests can run against saved fixtures.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from typing import Any

import httpx


class UnifiError(Exception):
    """Raised for transport errors, non-2xx responses and malformed payloads."""


@dataclass(frozen=True)
class UnifiNetwork:
    unifi_id: str
    name: str
    vlan_tag: int | None  # None = untagged default LAN
    subnet: str  # network CIDR, e.g. "10.1.10.0/24"
    gateway: str  # "10.1.10.1"
    dhcp_start: str | None
    dhcp_end: str | None


@dataclass(frozen=True)
class UnifiClient:
    mac: str  # lowercase, colon separated
    name: str | None  # user-set name in UniFi
    hostname: str | None
    vendor: str | None
    ip: str | None  # current IP if online, else last known
    fixed_ip: str | None
    note: str | None
    network_id: str | None  # UnifiNetwork.unifi_id
    first_seen: int | None  # epoch seconds
    last_seen: int | None
    online: bool
    user_id: str | None = None  # UniFi's id for the known-client record (needed to update it)
    dns_name: str | None = None  # UniFi's Local DNS Record, when enabled
    gear_model: str | None = None  # set for UniFi's own gear (switch, access point, gateway)
    dns_record_id: str | None = None  # gear only: the DNS record that names it


@dataclass(frozen=True)
class DnsRecord:
    """An A record in UniFi's DNS records list (Settings > Policy table > DNS records)."""

    id: str
    name: str
    ip: str
    enabled: bool


@dataclass(frozen=True)
class UnifiInfo:
    version: str


# ---------------------------------------------------------------- parsing (pure)


def _data(payload: Any, what: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("meta", {}).get("rc") != "ok":
        raise UnifiError(f"{what}: unexpected response envelope")
    data = payload.get("data")
    if not isinstance(data, list):
        raise UnifiError(f"{what}: missing data list")
    return data


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _dns(row: dict[str, Any]) -> str | None:
    return _str(row.get("local_dns_record")) if row.get("local_dns_record_enabled") else None


GEAR_TYPES = {
    "usw": "Switch",
    "uap": "Access point",
    "udm": "Gateway",
    "ugw": "Gateway",
    "uxg": "Gateway",
    "ucg": "Gateway",
    "uck": "Cloud Key",
}


def _gear_kind(row: dict[str, Any]) -> str:
    """Readable device type plus model code, e.g. 'Switch · US48PRO'."""
    kind = GEAR_TYPES.get(str(row.get("type") or ""), _str(row.get("type")) or "UniFi device")
    model = _str(row.get("model"))
    return f"{kind} · {model}" if model else kind


def parse_gear(payload: Any) -> list[UnifiClient]:
    """UniFi's own devices (stat/device), in the same shape as clients.

    A static address in the device's network settings counts as its fixed IP; state 1 is
    connected. These are not clients: they have no client record to edit or forget.
    """
    out: list[UnifiClient] = []
    for row in _data(payload, "stat/device"):
        if not row.get("mac"):
            continue
        cfg = row.get("config_network") or {}
        fixed = _str(cfg.get("ip")) if cfg.get("type") == "static" else None
        out.append(
            UnifiClient(
                mac=str(row["mac"]).lower(),
                name=_str(row.get("name")) or _str(row.get("model")),
                hostname=None,
                vendor=None,
                ip=_str(row.get("ip")),
                fixed_ip=fixed,
                note=None,
                network_id=None,  # placed by address
                first_seen=None,
                last_seen=_int(row.get("last_seen")),
                online=row.get("state") == 1,
                gear_model=_gear_kind(row),
            )
        )
    return out


def parse_dns_records(payload: Any) -> list[DnsRecord]:
    if not isinstance(payload, list):
        raise UnifiError("static-dns: unexpected response")
    return [
        DnsRecord(
            str(r["_id"]),
            str(r.get("key") or ""),
            str(r.get("value") or ""),
            bool(r.get("enabled")),
        )
        for r in payload
        if r.get("record_type") == "A" and r.get("_id")
    ]


def name_gear(gear: list[UnifiClient], records: list[DnsRecord]) -> list[UnifiClient]:
    """Give each piece of gear the name of the enabled A record pointing at its address."""
    by_ip: dict[str, DnsRecord] = {}
    for r in sorted(records, key=lambda r: r.name):
        if r.enabled:
            by_ip.setdefault(r.ip, r)
    out = []
    for g in gear:
        r = by_ip.get(g.fixed_ip or g.ip or "")
        out.append(replace(g, dns_name=r.name, dns_record_id=r.id) if r else g)
    return out


def parse_info(payload: Any) -> UnifiInfo:
    row = _data(payload, "sysinfo")[0]
    return UnifiInfo(version=str(row.get("version", "?")))


def parse_networks(payload: Any) -> list[UnifiNetwork]:
    out: list[UnifiNetwork] = []
    for row in _data(payload, "networkconf"):
        if row.get("purpose") != "corporate" or not row.get("ip_subnet"):
            continue  # skips WAN, VPN and anything without an IPv4 subnet
        iface = ipaddress.ip_interface(row["ip_subnet"])
        if iface.version != 4:
            continue
        dhcp_on = bool(row.get("dhcpd_enabled"))
        out.append(
            UnifiNetwork(
                unifi_id=str(row["_id"]),
                name=str(row.get("name") or row["_id"]),
                vlan_tag=_int(row.get("vlan")) if row.get("vlan_enabled") else None,
                subnet=str(iface.network),
                gateway=str(iface.ip),
                dhcp_start=_str(row.get("dhcpd_start")) if dhcp_on else None,
                dhcp_end=_str(row.get("dhcpd_stop")) if dhcp_on else None,
            )
        )
    return out


def _client_from_user(row: dict[str, Any]) -> UnifiClient:
    return UnifiClient(
        mac=str(row["mac"]).lower(),
        name=_str(row.get("name")),
        hostname=_str(row.get("hostname")),
        vendor=_str(row.get("oui")),
        ip=_str(row.get("last_ip")),
        fixed_ip=_str(row.get("fixed_ip")) if row.get("use_fixedip") else None,
        note=_str(row.get("note")),  # key is absent when unset
        network_id=_str(row.get("last_connection_network_id")),
        first_seen=_int(row.get("first_seen")),
        last_seen=_int(row.get("last_seen")),
        online=False,
        user_id=_str(row.get("_id")),
        dns_name=_dns(row),
    )


def parse_clients(user_payload: Any, sta_payload: Any) -> list[UnifiClient]:
    """Known clients from rest/user, refreshed with live data from stat/sta."""
    by_mac: dict[str, UnifiClient] = {}
    for row in _data(user_payload, "rest/user"):
        c = _client_from_user(row)
        by_mac[c.mac] = c
    for row in _data(sta_payload, "stat/sta"):
        mac = str(row["mac"]).lower()
        base = by_mac.get(mac) or _client_from_user(row)
        live_fixed = _str(row.get("fixed_ip")) if row.get("use_fixedip") else None
        seen = [t for t in (base.last_seen, _int(row.get("last_seen"))) if t is not None]
        by_mac[mac] = UnifiClient(
            mac=mac,
            name=_str(row.get("name")) or base.name,
            hostname=_str(row.get("hostname")) or base.hostname,
            vendor=_str(row.get("oui")) or base.vendor,
            ip=_str(row.get("ip")) or base.ip,
            fixed_ip=live_fixed or base.fixed_ip,
            note=_str(row.get("note")) or base.note,
            network_id=_str(row.get("network_id")) or base.network_id,
            first_seen=base.first_seen or _int(row.get("first_seen")),
            last_seen=max(seen) if seen else None,
            online=True,
            user_id=base.user_id or _str(row.get("user_id")),
            dns_name=_dns(row) or base.dns_name,
        )
    return sorted(by_mac.values(), key=lambda c: c.mac)


# ---------------------------------------------------------------- transport


class UnifiAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        site: str = "default",
        verify_tls: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self._prefix = f"/proxy/network/api/s/{site}"
        self._v2 = f"/proxy/network/v2/api/site/{site}"
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            verify=verify_tls,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, path: str, body: dict[str, Any]) -> Any:
        try:
            r = self._client.request(method, self._prefix + path, json=body)
        except httpx.HTTPError as exc:
            raise UnifiError(f"{method} {path}: {exc.__class__.__name__}: {exc}") from exc
        try:
            payload = r.json()
        except ValueError as exc:
            raise UnifiError(f"{method} {path}: HTTP {r.status_code}, invalid JSON") from exc
        if r.status_code != 200 or payload.get("meta", {}).get("rc") != "ok":
            msg = payload.get("meta", {}).get("msg", f"HTTP {r.status_code}")
            raise UnifiError(f"{method} {path}: {msg}")
        return payload

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._send("POST", path, body)

    def _get(self, path: str) -> Any:
        try:
            r = self._client.get(self._prefix + path)
        except httpx.HTTPError as exc:
            raise UnifiError(f"GET {path}: {exc.__class__.__name__}: {exc}") from exc
        if r.status_code != 200:
            raise UnifiError(f"GET {path}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise UnifiError(f"GET {path}: invalid JSON") from exc

    def info(self) -> UnifiInfo:
        return parse_info(self._get("/stat/sysinfo"))

    def networks(self) -> list[UnifiNetwork]:
        return parse_networks(self._get("/rest/networkconf"))

    def _call_v2(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """The newer v2 API: plain JSON, no meta envelope; errors carry a message."""
        try:
            r = self._client.request(method, self._v2 + path, json=body)
        except httpx.HTTPError as exc:
            raise UnifiError(f"{method} {path}: {exc.__class__.__name__}: {exc}") from exc
        try:
            payload = r.json() if r.content else None
        except ValueError as exc:
            raise UnifiError(f"{method} {path}: HTTP {r.status_code}, invalid JSON") from exc
        if r.status_code != 200:
            msg = payload.get("message") if isinstance(payload, dict) else None
            raise UnifiError(f"{method} {path}: {msg or f'HTTP {r.status_code}'}")
        return payload

    def dns_records(self) -> list[DnsRecord]:
        return parse_dns_records(self._call_v2("GET", "/static-dns"))

    def gear(self) -> list[UnifiClient]:
        return parse_gear(self._get("/stat/device"))

    def clients(self) -> list[UnifiClient]:
        return parse_clients(self._get("/rest/user"), self._get("/stat/sta"))

    # ------------------------------------------------------------ writes

    def create_dns_record(self, name: str, ip: str) -> str:
        """Verified on Network 10.6.106: POST v2 static-dns. UniFi accepts duplicate names,
        so callers check uniqueness. The name resolves via the gateway after 5 to 10 seconds."""
        body = {"key": name, "record_type": "A", "value": ip, "enabled": True}
        return str(self._call_v2("POST", "/static-dns", body)["_id"])

    def update_dns_record(self, record_id: str, name: str, ip: str) -> None:
        body = {"_id": record_id, "key": name, "record_type": "A", "value": ip, "enabled": True}
        self._call_v2("PUT", f"/static-dns/{record_id}", body)

    def delete_dns_record(self, record_id: str) -> None:
        self._call_v2("DELETE", f"/static-dns/{record_id}")

    def update_client(
        self,
        user_id: str,
        *,
        name: str | None = None,
        note: str | None = None,
        fixed_ip: str | None = None,
        clear_fixed_ip: bool = False,
        network_id: str | None = None,
        dns_name: str | None = None,
        clear_dns_name: bool = False,
    ) -> None:
        """Change name, note, fixed IP and/or local DNS record of a known client, as the
        UniFi client page does.

        Only the fields passed are sent. PUT rest/user/{id}; verified against Network 10.6.106
        with an unchanged name, note and fixed IP. The reply carries no data, so re-sync after.
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if note is not None:
            body["note"] = note
            body["noted"] = bool(note)
        if fixed_ip is not None:
            body.update(use_fixedip=True, fixed_ip=fixed_ip)
            if network_id:
                body["network_id"] = network_id
        elif clear_fixed_ip:
            body["use_fixedip"] = False
        if dns_name is not None:
            body.update(local_dns_record_enabled=True, local_dns_record=dns_name)
        elif clear_dns_name:
            body.update(local_dns_record_enabled=False, local_dns_record="")
        if not body:
            return
        self._send("PUT", f"/rest/user/{user_id}", body)

    def forget_clients(self, macs: list[str]) -> None:
        """Remove known clients from UniFi (name, note and fixed IP go with them).

        Same as "Forget" in the UniFi client list. Verified against Network 10.6.106:
        POST cmd/stamgr {"cmd": "forget-sta", "macs": [...]}; an empty list is rejected
        with api.error.NoMacSpecified.
        """
        clean = [m.lower() for m in macs if m]
        if not clean:
            raise UnifiError("forget_clients: no MAC given")
        self._post("/cmd/stamgr", {"cmd": "forget-sta", "macs": clean})
