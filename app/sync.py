"""Pull networks and clients from UniFi into SQLite."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable

from app.unifi_adapter import UnifiAdapter, UnifiClient, UnifiError, UnifiNetwork, name_gear

log = logging.getLogger("ipam.sync")


def write_networks(conn: sqlite3.Connection, nets: Iterable[UnifiNetwork]) -> None:
    conn.executemany(
        """
        INSERT INTO vlan (unifi_id, name, vlan_tag, subnet, gateway, dhcp_start, dhcp_end)
        VALUES (:unifi_id, :name, :vlan_tag, :subnet, :gateway, :dhcp_start, :dhcp_end)
        ON CONFLICT(unifi_id) DO UPDATE SET
            name = excluded.name, vlan_tag = excluded.vlan_tag, subnet = excluded.subnet,
            gateway = excluded.gateway, dhcp_start = excluded.dhcp_start,
            dhcp_end = excluded.dhcp_end
        """,
        [n.__dict__ for n in nets],
    )


def write_clients(conn: sqlite3.Connection, clients: Iterable[UnifiClient]) -> None:
    # category is owned by this app and never touched here.
    conn.executemany(
        """
        INSERT INTO device (mac, name, hostname, vendor, first_seen, last_seen, last_ip,
                            fixed_ip, unifi_note, vlan_unifi_id, unifi_user_id, dns_name,
                            gear_model, dns_record_id, online)
        VALUES (:mac, :name, :hostname, :vendor, :first_seen, :last_seen, :ip,
                :fixed_ip, :note, :network_id, :user_id, :dns_name, :gear_model,
                :dns_record_id, :online)
        ON CONFLICT(mac) DO UPDATE SET
            name = excluded.name, hostname = excluded.hostname, vendor = excluded.vendor,
            first_seen = COALESCE(device.first_seen, excluded.first_seen),
            last_seen = MAX(COALESCE(device.last_seen, 0), COALESCE(excluded.last_seen, 0)),
            last_ip = COALESCE(excluded.last_ip, device.last_ip),
            fixed_ip = excluded.fixed_ip, unifi_note = excluded.unifi_note,
            vlan_unifi_id = COALESCE(excluded.vlan_unifi_id, device.vlan_unifi_id),
            unifi_user_id = COALESCE(excluded.unifi_user_id, device.unifi_user_id),
            dns_name = excluded.dns_name,
            gear_model = excluded.gear_model,
            dns_record_id = excluded.dns_record_id,
            online = excluded.online
        """,
        [{**c.__dict__, "online": int(c.online)} for c in clients],
    )


def set_state(conn: sqlite3.Connection, **kv: str | None) -> None:
    conn.executemany(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        list(kv.items()),
    )


def get_state(conn: sqlite3.Connection) -> dict[str, str | None]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM sync_state")}


def run_sync(conn: sqlite3.Connection, adapter: UnifiAdapter) -> None:
    """One sync pass. Raises UnifiError on failure after recording it."""
    now = str(int(time.time()))
    with conn:
        set_state(conn, paused_since=None)  # an attempt ends any pause, success or not
    try:
        info = adapter.info()
        nets = adapter.networks()
        clients = adapter.clients() + name_gear(adapter.gear(), adapter.dns_records())
    except UnifiError as exc:
        with conn:
            set_state(conn, last_attempt=now, last_error=str(exc))
        raise
    with conn:
        write_networks(conn, nets)
        write_clients(conn, clients)
        set_state(
            conn,
            last_attempt=now,
            last_success=now,
            last_error=None,
            unifi_version=info.version,
            vlan_count=str(len(nets)),
            device_count=str(len(clients)),
        )
    log.info("sync ok: %d networks, %d clients and UniFi devices", len(nets), len(clients))


def sync_once(db_path: str, adapter: UnifiAdapter) -> None:
    """Blocking sync with its own connection, safe to run via asyncio.to_thread."""
    from app.db import connect

    conn = connect(db_path)
    try:
        run_sync(conn, adapter)
    finally:
        conn.close()


def mark_paused(db_path: str) -> None:
    """Record that the background sync is paused because no page is being viewed."""
    from app.db import connect

    conn = connect(db_path)
    try:
        if not get_state(conn).get("paused_since"):
            with conn:
                set_state(conn, paused_since=str(int(time.time())))
            log.info("sync paused: no page viewed")
    finally:
        conn.close()


def is_paused(db_path: str) -> bool:
    from app.db import connect

    conn = connect(db_path)
    try:
        return bool(get_state(conn).get("paused_since"))
    finally:
        conn.close()


async def sync_loop(
    db_path: str,
    adapter: UnifiAdapter,
    interval: int,
    stop: asyncio.Event,
    idle: Callable[[], bool] = lambda: False,
) -> None:
    """Sync every interval; while idle() says nobody is viewing a page, skip and mark paused."""
    while not stop.is_set():
        if idle():
            await asyncio.to_thread(mark_paused, db_path)
        else:
            try:
                await asyncio.to_thread(sync_once, db_path, adapter)
            except UnifiError as exc:
                log.warning("sync failed: %s", exc)
            except Exception:
                log.exception("sync crashed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
