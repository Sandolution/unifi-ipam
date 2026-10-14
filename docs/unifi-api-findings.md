# UniFi API findings

Probed in October 2026 against a UniFi Dream Machine Pro with `curl` and the API key in header
`X-API-KEY`. These notes record what that controller accepted; other Network versions may differ.
Controller: UniFi Dream Machine Pro, **Network application 10.6.106** (build `atag_10.6.106_36011`),
single site `default`.
TLS: the gateway uses a self-signed certificate, so `VERIFY_TLS=false` is required unless the certificate is trusted.

## Authentication

The same API key (UniFi > Settings > Control Plane > Integrations) works for **both** surfaces below.
No cookie login, no CSRF token. Every probe returned HTTP 200 on first try; no 401/403 seen.

## Surface 1: official Integration API (`/proxy/network/integration/v1/...`)

| Endpoint | Result | Notes |
|---|---|---|
| `GET /info` | 200 | `{"applicationVersion":"10.6.106"}` |
| `GET /sites` | 200 | paginated `{offset,limit,count,totalCount,data[]}` |
| `GET /sites/{id}/networks` | 200 | **list has only** `id, name, enabled, vlanId, management, zoneId, default`. No subnet, no DHCP range. |
| `GET /sites/{id}/networks/{nid}` | 200 | detail **does** include `ipv4Configuration.hostIpAddress`, `prefixLength`, `dhcpConfiguration.ipAddressRange.start/stop`, `mode`. One call per network. |
| `GET /sites/{id}/clients` | 200 | **active clients only** (43). Fields: `id, type, name, connectedAt, ipAddress, macAddress, uplinkDeviceId, access`. No fixed IP, no note, no last_seen, no network id. |
| `GET /sites/{id}/clients/{cid}` | 200 | same fields as list, nothing extra. |
| `GET /sites/{id}/devices` | 200 | UniFi infrastructure (UDM, switch, APs), 5 items. Not needed. |
| `GET /sites/{id}/vlans` | 404 | endpoint does not exist. |

Verdict: works, but cannot supply fixed IP, notes, first/last seen, or offline clients. Not sufficient alone.

## Surface 2: legacy Network API via proxy (`/proxy/network/api/s/default/...`)

Response envelope is always `{"meta":{"rc":"ok"},"data":[...]}`. No pagination; full list in one call.

| Endpoint | Result | What we use |
|---|---|---|
| `GET /api/self` | 200 | smoke test only (admin profile). Not saved as fixture. |
| `GET /api/s/default/stat/sysinfo` | 200 | `version`, `build`, `hostname` for the status page. |
| `GET /api/s/default/rest/networkconf` | 200 | 10 records. Filter `purpose == "corporate"` to get the 7 LANs/VLANs. Fields: `_id, name, vlan, vlan_enabled, ip_subnet` ("10.1.10.1/24", gateway form), `dhcpd_enabled, dhcpd_start, dhcpd_stop, enabled`. WAN (`purpose=wan`) and VPN (`purpose=remote-user-vpn`) records are skipped. Default LAN has `vlan: null` (untagged) and `dhcpd_enabled: false`. |
| `GET /api/s/default/rest/user` | 200 | **all known clients**, 82 records, online and offline. Fields: `mac, name, hostname, oui, use_fixedip, fixed_ip, noted, note, first_seen, last_seen, last_ip, last_connection_network_id, last_connection_network_name, is_wired, dev_vendor` (int fingerprint id, not a name). `note` key is **absent** when not set (present on 13 of 82). `last_ip` missing on 5 records. `network_id` is always null here; use `last_connection_network_id` (present on all 82). |
| `GET /api/s/default/stat/device` | 200 | UniFi's own gear (gateway, switches, access points), 5 records. Used since 2026-10-12: `mac`, `ip`, `name`, `model`, `state` (1 = connected), `last_seen`, and `config_network.type`/`.ip` (a `static` address counts as fixed). The full response is about 220 KB and contains credentials and keys; the fixture keeps only those fields. The UDM reports its internet address here; its LAN addresses are the VLAN gateways. |
| `GET /api/s/default/stat/alluser` | 200 | byte-identical to `rest/user` on this controller. Not used. |
| `GET /api/s/default/stat/sta` | 200 | **active clients**, 43 records, superset of the matching `rest/user` records plus live `ip`, `network_id`, `network`, `uptime`, signal stats. 2 active clients have no `ip` (L2 only / not yet leased). All 43 MACs also appear in `rest/user`. |

Also reachable but not used (richer but undocumented and more volatile):
`/proxy/network/v2/api/site/default/clients/active` and `.../clients/history`.

## UniFi's DNS records list (v2 API), tested 2026-10-12

`/proxy/network/v2/api/site/default/static-dns` holds DNS records independent of clients
(UniFi: Settings > Policy table > DNS records). Plain JSON, no `meta` envelope; errors are
`{"code", "message", "errorCode"}` with HTTP 4xx. The app uses it only to name UniFi gear.

| Call | Result on Network 10.6.106 |
|---|---|
| `GET` | list of records: `_id, key, record_type, value, enabled, ttl, port, priority, weight` |
| `POST {key, record_type: "A", value, enabled: true}` | 200, returns the record with its `_id` |
| `PUT /{id}` with the full record | 200, rename works |
| `DELETE /{id}` | 200 |
| `POST` with a name already in use | 200: duplicates are accepted, so the app checks uniqueness itself |
| `POST` with `key: "bad name"` | 400 `api.err.StaticDnsRecordInvalidParameters`, "Invalid Hostname" |

A new record resolved via the gateway after 5 to 10 seconds, not immediately. All
test records were deleted afterwards; the list was empty before and after the test.

## Writes to clients (internal API)

| Call | Result |
|---|---|
| `PUT /api/s/default/rest/user/{_id}` with `name`, `note`, `noted`, `use_fixedip`, `fixed_ip` | 200 with `{"meta":{"rc":"ok"},"data":[]}`. Sent with the client's unchanged values; the record was identical before and after. The reply carries no data, so the app syncs after a save. |
| `POST /api/s/default/cmd/stamgr` `{"cmd":"forget-sta","macs":[]}` | 400 `api.error.NoMacSpecified`: the command exists and the key may use it. Probed with an empty list, so nothing was removed. |

Real changes (a new name, note, fixed IP or local DNS record) and clearing them were not sent to
a controller; they are covered by tests against a stand-in. The field names for the local DNS
record (`local_dns_record_enabled`, `local_dns_record`) come from the client records.

## Decision for the adapter

Use the **internal (legacy) API** for all data:

1. VLANs: `rest/networkconf`, filtered to `purpose == "corporate"`.
2. Known clients: `rest/user` (gives fixed IP, note, first/last seen, last IP, last network).
3. Active clients: `stat/sta`, merged over `rest/user` by MAC to refresh current `ip`, `network_id` and `last_seen`.
4. UniFi's own devices: `stat/device`, placed in VLANs by address.
5. DNS names for UniFi devices: the v2 `static-dns` list, matched to devices by address.

Reasons: one surface, one auth header, one response envelope, and it is the only one exposing fixed IP, notes
and offline clients. The integration API is recorded above so the adapter can switch to it later for networks
if the legacy endpoints are removed (network detail there has subnet + DHCP, it just costs one call per network).

Vendor: `oui` string from the legacy API is used as `vendor`. `dev_vendor` is a numeric fingerprint id without
a public name table, so it is ignored.

VLAN membership of a device: `last_connection_network_id` from `rest/user`, overridden by `network_id` from
`stat/sta` when the client is online. Fallback when both are missing: match `last_ip` / `fixed_ip` to a VLAN subnet.

## Fixtures (`tests/fixtures/`)

Recorded from the controller above, then processed by `scripts/anonymize_fixtures.py`:

- **Secrets removed.** Every key starting with `x_` or naming a password, private key, secret,
  token or hash is gone. The API key does not appear in any file.
- **Large files trimmed.** `stat/device` and `stat/sysinfo` keep only the fields the app reads.
- **Anonymized.** Names, domains, the Wi-Fi network name, the public address, MACs and
  identifying keys were replaced.

Record counts, address layout and field shapes are real. Names, MACs and domains are not.

| File | Source |
|---|---|
| `legacy_networkconf.json` | `rest/networkconf` |
| `legacy_rest_user.json` | `rest/user` |
| `legacy_stat_sta.json` | `stat/sta` |
| `legacy_stat_sysinfo.json` | `stat/sysinfo`, trimmed to version fields |
| `legacy_stat_device.json` | `stat/device`, trimmed to the fields listed above |
| `integration_info.json`, `integration_sites.json`, `integration_networks.json`, `integration_network_detail.json`, `integration_clients.json` | integration API, kept for reference |

## Not verified

- Real (non-identical) client updates, and clearing a name, note or DNS record.
- Behaviour with more than one site (this controller has one).
- Whether `rest/user` ever paginates on large installs (82 records returned in one response here).
- Legacy API stability across future Network releases; it is unofficial.
