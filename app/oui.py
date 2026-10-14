"""MAC vendor fallback from the bundled IEEE registry (see scripts/update_oui.py)."""

from __future__ import annotations

import gzip
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "oui.tsv.gz"


@cache
def _table() -> dict[str, str]:
    if not DATA.exists():
        return {}
    out: dict[str, str] = {}
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        for line in fh:
            prefix, _, name = line.rstrip("\n").partition("\t")
            if name:
                out[prefix] = name
    return out


def is_private(mac: str) -> bool:
    """Locally administered (randomised) MAC: second bit of the first octet set."""
    try:
        return bool(int(mac[:2], 16) & 0x02)
    except ValueError:
        return False


def lookup(mac: str) -> str | None:
    if is_private(mac):
        return None
    return _table().get(mac.replace(":", "").replace("-", "").upper()[:6])
