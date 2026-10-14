# Development

## Setup

The app is Python 3.12 with FastAPI, SQLite, Jinja2 templates and htmx. There is no
JavaScript build step. With [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
```

Run it against your controller:

```sh
export UNIFI_URL=https://192.168.1.1 UNIFI_API_KEY=... VERIFY_TLS=false DB_PATH=./data/ipam.db
.venv/bin/uvicorn app.asgi:app --reload
```

On Windows, use `.venv\Scripts\` instead of `.venv/bin/`.

To run it without a controller, leave `UNIFI_URL` empty: the background sync stays off and the
pages show whatever is in the database.

## Tests

```sh
.venv/bin/pytest                      # Python: adapter, sync, checks, pages and routes
.venv/bin/ruff check . && .venv/bin/ruff format --check .
node tests/js/run.js                  # browser-side behaviour of app/static/app.js
```

The Python tests never contact a controller. They run against recorded, anonymized responses in
`tests/fixtures/` and mock every write. The browser-side checks run `app/static/app.js` against
a simulated page in Node 18 or newer, without any packages. They cover the Start/End/Size
calculation, row-click zoom, banners, busy states, save-and-reload, and refresh on return.

## Code layout

| File | What it holds |
|---|---|
| `app/unifi_adapter.py` | Every call to UniFi, returning plain dataclasses |
| `app/sync.py` | Pulling UniFi data into SQLite, and the idle pause |
| `app/db.py` | Schema and migrations |
| `app/models.py` | The data shapes: VLAN, range, device, row, cell, zoom |
| `app/store.py` | Reading and writing SQLite, with range, fixed-IP and DNS-name validation |
| `app/strip.py` | The address strip: cells, colours and exact label positions |
| `app/views.py` | What a VLAN page shows: device placement, stats, zoom and grouping |
| `app/checks.py` | The four checks and the ignore list |
| `app/ipmath.py`, `app/oui.py` | IPv4 arithmetic, and the MAC vendor fallback |
| `app/main.py` | Routes and templates |
| `app/static/app.js` | Small page behaviours, with no framework |
| `scripts/` | Refreshing the vendor registry, and anonymizing test data |

## Design decisions

- **The internal API, not the Integration API.** Only the internal endpoints expose fixed IPs,
  notes, first and last seen, and offline clients. The details are in
  [unifi-api-findings.md](unifi-api-findings.md).
- **SQLite with the standard library.** There are a handful of tables and queries; an ORM would
  add more than it saves.
- **The database is a cache plus your planning.** UniFi owns devices and networks. The app owns
  ranges, categories and ignored findings. Sync never deletes rows, so a device that disappears
  for a while keeps its category.
- **One current address per device.** That is the live address when online, otherwise the fixed
  reservation, otherwise the last lease. The checks count only addresses a device actually holds:
  online on them, or reserved for them. An offline device's old lease counts for nothing, because
  DHCP reuses it.
- **Inherited categories are computed, not stored.** Changing a range's category changes it for
  every device in the range that has no category of its own.
- **UniFi gear is read from the device list.** Switches and access points have no client record.
  Their DNS name is therefore an A record in UniFi's custom DNS records, while clients use their
  per-device local DNS record.
- **A strip with one cell per address,** rather than proportional bars. Labels are positioned with
  the same arithmetic the browser uses for the cells, so they line up at every width.
- **htmx and the vendor registry are bundled.** The app needs no internet access at runtime, apart
  from Google Fonts, which fall back to system fonts.

## Refreshing the test data

The fixtures are real responses from a controller, stripped of secrets and anonymized. To record
your own, download the raw responses into `tests/fixtures/`, overwriting the existing ones:

```sh
export UNIFI_URL=https://192.168.1.1 UNIFI_API_KEY=...
for ep in rest/networkconf rest/user stat/sta stat/sysinfo stat/device; do
  curl -sk -H "X-API-KEY: $UNIFI_API_KEY" "$UNIFI_URL/proxy/network/api/s/default/$ep" \
    > "tests/fixtures/legacy_$(echo $ep | tr / _).json"
done
```

Then run the anonymizer before anything else touches those files:

```sh
python -I scripts/anonymize_fixtures.py tests/fixtures \
  --replace "YourName=Alex" --replace "yourdomain.com=example.com" --wifi HomeWiFi \
  --map-out /tmp/mac-map.json
```

It removes secret fields, trims the device list and system info to the fields the app reads, and
replaces every MAC address. It keeps the maker prefix, so vendor lookup still works. It also
replaces the Wi-Fi network name and public addresses, removes identifying keys, and applies your
`--replace` pairs. Add a pair for every personal name, domain or serial number in device names
and hostnames.

Afterwards, search the fixtures for your API key and your names before committing. Tests that
name specific devices or MACs will need updating; `/tmp/mac-map.json` lists which MAC became
which. Delete that file when you're done, since it undoes the MAC anonymization.

## Vendor registry

`app/data/oui.tsv.gz` is a compact copy of the IEEE MA-L registry, used when UniFi reports no
vendor. Refresh it with:

```sh
python -I scripts/update_oui.py
```
