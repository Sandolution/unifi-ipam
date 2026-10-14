"""Rebuild app/data/oui.tsv.gz from the IEEE MA-L registry.

UniFi's own vendor table lags behind the registry, so the app falls back to this file when
UniFi reports no vendor for a hardware MAC.

    python -I scripts/update_oui.py                     # download from IEEE
    python -I scripts/update_oui.py --source oui.csv    # use a file you downloaded yourself

Stdlib only. The output is one "AABBCC<TAB>Organization" line per assignment, gzip-compressed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://standards-oui.ieee.org/oui/oui.csv"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "oui.tsv.gz"
HEX6 = re.compile(r"^[0-9A-F]{6}$")


def fetch() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": "unifi-ipam oui updater"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        return resp.read().decode("utf-8", errors="replace")


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        prefix = (row.get("Assignment") or "").strip().upper()
        name = " ".join((row.get("Organization Name") or "").split())
        if row.get("Registry") == "MA-L" and HEX6.match(prefix) and name:
            out[prefix] = name.replace("\t", " ")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, help="local copy of oui.csv instead of downloading")
    args = ap.parse_args()
    text = args.source.read_text(encoding="utf-8", errors="replace") if args.source else fetch()
    table = parse(text)
    if len(table) < 10_000:
        print(f"refusing to write: only {len(table)} entries parsed", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}\t{v}\n" for k, v in sorted(table.items()))
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write(body)
    print(f"wrote {len(table)} prefixes to {OUT} ({OUT.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
