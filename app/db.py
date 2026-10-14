"""SQLite schema and connection helper (stdlib sqlite3)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("ipam.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vlan (
    id          INTEGER PRIMARY KEY,
    unifi_id    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    vlan_tag    INTEGER,
    subnet      TEXT NOT NULL,
    gateway     TEXT NOT NULL,
    dhcp_start  TEXT,
    dhcp_end    TEXT
);
CREATE TABLE IF NOT EXISTS ip_range (
    id        INTEGER PRIMARY KEY,
    vlan_id   INTEGER NOT NULL REFERENCES vlan(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    start_ip  TEXT NOT NULL,
    end_ip    TEXT NOT NULL,
    category  TEXT NOT NULL DEFAULT '',
    note      TEXT NOT NULL DEFAULT '',
    UNIQUE (vlan_id, name)
);
CREATE TABLE IF NOT EXISTS device (
    mac            TEXT PRIMARY KEY,
    name           TEXT,
    hostname       TEXT,
    vendor         TEXT,
    category       TEXT NOT NULL DEFAULT '',
    first_seen     INTEGER,
    last_seen      INTEGER,
    last_ip        TEXT,
    fixed_ip       TEXT,
    unifi_note     TEXT,
    vlan_unifi_id  TEXT,
    unifi_user_id  TEXT,
    dns_name       TEXT,
    gear_model     TEXT,
    dns_record_id  TEXT,
    online         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS check_ignore (
    kind     TEXT NOT NULL,     -- range | dup | stale
    subject  TEXT NOT NULL,     -- device MAC, or "<vlan id>:<ip>" for an address conflict
    created  INTEGER NOT NULL,
    PRIMARY KEY (kind, subject)
);
CREATE TABLE IF NOT EXISTS sync_state (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(device)")}
    if "unifi_user_id" not in cols:
        conn.execute("ALTER TABLE device ADD COLUMN unifi_user_id TEXT")
    if "dns_name" not in cols:
        conn.execute("ALTER TABLE device ADD COLUMN dns_name TEXT")
    if "gear_model" not in cols:
        conn.execute("ALTER TABLE device ADD COLUMN gear_model TEXT")
    if "dns_record_id" not in cols:
        conn.execute("ALTER TABLE device ADD COLUMN dns_record_id TEXT")
    # The app's own device text (first "tags", then "note") was replaced by UniFi's note.
    # Drop the old column only when it holds nothing; otherwise keep it so no text is lost.
    for old in ("tags", "note"):
        if old not in cols:
            continue
        used = conn.execute(f"SELECT count(*) FROM device WHERE {old} != ''").fetchone()[0]
        if used:
            log.warning("keeping device.%s: %d rows still hold text", old, used)
        else:
            conn.execute(f"ALTER TABLE device DROP COLUMN {old}")


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
