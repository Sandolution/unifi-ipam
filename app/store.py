"""Reading and writing the SQLite database: VLANs, ranges and devices.

Validation that needs the database (range overlap, free fixed IP) lives here too.
"""

from __future__ import annotations

import re
import sqlite3

from app import ipmath, oui
from app.ipmath import Addr, RangeCheck
from app.models import Device, IpRange, Vlan


def _addr(v: str | None) -> Addr | None:
    if not v:
        return None
    try:
        return ipmath.parse_addr(v)
    except ValueError:
        return None


def _vlan(row: sqlite3.Row) -> Vlan:
    s, e = _addr(row["dhcp_start"]), _addr(row["dhcp_end"])
    return Vlan(
        id=row["id"],
        unifi_id=row["unifi_id"],
        name=row["name"],
        vlan_tag=row["vlan_tag"],
        net=ipmath.parse_network(row["subnet"]),
        gateway=ipmath.parse_addr(row["gateway"]),
        dhcp=(s, e) if s and e else None,
    )


def _range(row: sqlite3.Row) -> IpRange:
    return IpRange(
        id=row["id"],
        vlan_id=row["vlan_id"],
        name=row["name"],
        start=ipmath.parse_addr(row["start_ip"]),
        end=ipmath.parse_addr(row["end_ip"]),
        category=row["category"],
        note=row["note"],
    )


def _device(row: sqlite3.Row) -> Device:
    return Device(
        mac=row["mac"],
        name=row["name"],
        hostname=row["hostname"],
        vendor=row["vendor"] or oui.lookup(row["mac"]),
        category=row["category"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        last_ip=_addr(row["last_ip"]),
        fixed_ip=_addr(row["fixed_ip"]),
        unifi_note=row["unifi_note"],
        vlan_unifi_id=row["vlan_unifi_id"],
        online=bool(row["online"]),
        unifi_user_id=row["unifi_user_id"],
        dns_name=row["dns_name"],
        gear_model=row["gear_model"],
        dns_record_id=row["dns_record_id"],
    )


def list_vlans(conn: sqlite3.Connection) -> list[Vlan]:
    rows = conn.execute("SELECT * FROM vlan ORDER BY vlan_tag IS NOT NULL, vlan_tag, name")
    return [_vlan(r) for r in rows]


def get_vlan(conn: sqlite3.Connection, vlan_id: int) -> Vlan | None:
    row = conn.execute("SELECT * FROM vlan WHERE id = ?", (vlan_id,)).fetchone()
    return _vlan(row) if row else None


def list_ranges(conn: sqlite3.Connection, vlan_id: int | None = None) -> list[IpRange]:
    sql = "SELECT * FROM ip_range"
    args: tuple[int, ...] = ()
    if vlan_id is not None:
        sql += " WHERE vlan_id = ?"
        args = (vlan_id,)
    rows = conn.execute(sql + " ORDER BY vlan_id, start_ip", args)
    return sorted((_range(r) for r in rows), key=lambda r: (r.vlan_id, r.start))


def get_range(conn: sqlite3.Connection, range_id: int) -> IpRange | None:
    row = conn.execute("SELECT * FROM ip_range WHERE id = ?", (range_id,)).fetchone()
    return _range(row) if row else None


def list_devices(conn: sqlite3.Connection) -> list[Device]:
    return [_device(r) for r in conn.execute("SELECT * FROM device")]


def get_device(conn: sqlite3.Connection, mac: str) -> Device | None:
    row = conn.execute("SELECT * FROM device WHERE mac = ?", (mac.lower(),)).fetchone()
    return _device(row) if row else None


def categories(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT category FROM ip_range UNION SELECT category FROM device ORDER BY 1"
    )
    return [r[0] for r in rows if r[0]]


def validate_range(
    conn: sqlite3.Connection,
    vlan: Vlan,
    name: str,
    start: Addr,
    end: Addr,
    exclude_id: int | None = None,
) -> RangeCheck:
    others = [(r.name, r.start, r.end) for r in list_ranges(conn, vlan.id) if r.id != exclude_id]
    check = ipmath.check_range(vlan.net, start, end, others, vlan.dhcp)
    if not name.strip():
        check.errors.append("name is required")
    elif any(n == name.strip() for n, _, _ in others):
        check.errors.append(f"a range named '{name.strip()}' already exists in this VLAN")
    return check


def save_range(
    conn: sqlite3.Connection,
    vlan_id: int,
    name: str,
    start_ip: str,
    end_ip: str,
    category: str,
    note: str,
    range_id: int | None = None,
) -> RangeCheck:
    vlan = get_vlan(conn, vlan_id)
    if vlan is None:
        return RangeCheck(["VLAN not found"], [])
    try:
        start, end = ipmath.parse_addr(start_ip), ipmath.parse_addr(end_ip)
    except ValueError as exc:
        return RangeCheck([f"invalid address: {exc}"], [])
    check = validate_range(conn, vlan, name, start, end, exclude_id=range_id)
    if not check.ok:
        return check
    params = (vlan_id, name.strip(), str(start), str(end), category.strip(), note.strip())
    with conn:
        if range_id is None:
            conn.execute(
                "INSERT INTO ip_range (vlan_id, name, start_ip, end_ip, category, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                params,
            )
        else:
            conn.execute(
                "UPDATE ip_range SET vlan_id=?, name=?, start_ip=?, end_ip=?, category=?, note=? "
                "WHERE id=?",
                (*params, range_id),
            )
    return check


def delete_range(conn: sqlite3.Connection, range_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM ip_range WHERE id = ?", (range_id,))


def delete_device(conn: sqlite3.Connection, mac: str) -> None:
    with conn:
        conn.execute("DELETE FROM device WHERE mac = ?", (mac.lower(),))


def check_fixed_ip(
    conn: sqlite3.Connection, vlan: Vlan, mac: str, ip: Addr
) -> tuple[list[str], list[str]]:
    """Rules for a new fixed IP: inside the subnet, not the gateway, not claimed by another
    device (online on it or reserved for it). Inside the DHCP pool is allowed with a warning."""
    errors: list[str] = []
    warnings: list[str] = []
    lo, hi = ipmath.host_bounds(vlan.net)
    if ip not in vlan.net:
        errors.append(f"{ip} is outside {vlan.net}")
    elif not lo <= ip <= hi:
        errors.append(f"{ip} is the network or broadcast address")
    elif ip == vlan.gateway:
        errors.append(f"{ip} is the gateway")
    else:
        for d in list_devices(conn):
            if d.mac != mac.lower() and ip in d.claims:
                how = "is online on it" if d.online and d.last_ip == ip else "has it reserved"
                errors.append(f"{ip} is taken: {d.display_name} ({d.mac}) {how}")
        if vlan.dhcp and vlan.dhcp[0] <= ip <= vlan.dhcp[1]:
            warnings.append(f"{ip} is inside the DHCP pool")
    return errors, warnings


HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def check_dns_name(conn: sqlite3.Connection, mac: str, name: str) -> list[str]:
    """A DNS name must be a valid hostname and not used by another device."""
    if not HOSTNAME.match(name):
        return [
            f"{name} is not a valid DNS name: use letters, digits and hyphens, "
            "with dots between labels"
        ]
    for d in list_devices(conn):
        if d.mac != mac.lower() and (d.dns_name or "").lower() == name:
            return [f"{name} is already used by {d.display_name} ({d.mac})"]
    return []


def update_device(conn: sqlite3.Connection, mac: str, category: str) -> None:
    with conn:
        conn.execute(
            "UPDATE device SET category = ? WHERE mac = ?",
            (category.strip(), mac.lower()),
        )
