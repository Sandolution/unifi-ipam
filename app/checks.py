"""Consistency checks over the SQLite data. Pure reads, apart from the ignore list."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from app import models, store, views
from app.ipmath import Addr
from app.models import Device, IpRange, Vlan

# ignore-list keys: out of category range, not in any range, address conflict, stale
KINDS = ("range", "norange", "dup", "stale")


@dataclass(frozen=True)
class Finding:
    """One device with a problem: outside its category's range, or a stale reservation."""

    vlan: Vlan
    device: Device
    ip: Addr | None
    detail: str


@dataclass(frozen=True)
class Conflict:
    """Two or more devices claiming one address in a VLAN."""

    vlan: Vlan
    ip: Addr
    devices: list[Device]

    @property
    def subject(self) -> str:
        return f"{self.vlan.id}:{self.ip}"


@dataclass
class CheckReport:
    out_of_range: list[Finding] = field(default_factory=list)
    unplanned: list[Finding] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    stale_fixed: list[Finding] = field(default_factory=list)
    ignored: set[tuple[str, str]] = field(default_factory=set)

    def is_ignored(self, kind: str, subject: str) -> bool:
        return (kind, subject) in self.ignored

    def counts(self) -> dict[str, tuple[int, int]]:
        """Per kind: (active, ignored). Ignored findings stay listed but are not counted."""
        subjects = {
            "range": [f.device.mac for f in self.out_of_range],
            "norange": [f.device.mac for f in self.unplanned],
            "dup": [c.subject for c in self.conflicts],
            "stale": [f.device.mac for f in self.stale_fixed],
        }
        out = {}
        for kind, items in subjects.items():
            ignored = sum(1 for s in items if (kind, s) in self.ignored)
            out[kind] = (len(items) - ignored, ignored)
        return out

    @property
    def total(self) -> int:
        return sum(active for active, _ in self.counts().values())


def check_out_of_range(
    vlan: Vlan, rows: list[models.DeviceRow], ranges: list[IpRange]
) -> list[Finding]:
    """Device has a category, this VLAN has ranges for it, but its IP is not in one of them."""
    by_cat: dict[str, list[IpRange]] = {}
    for r in ranges:
        if r.category:
            by_cat.setdefault(r.category, []).append(r)
    out: list[Finding] = []
    for row in rows:
        d = row.device
        if not d.category or row.ip is None or d.category not in by_cat:
            continue
        allowed = by_cat[d.category]
        if any(r.start <= row.ip <= r.end for r in allowed):
            continue
        names = ", ".join(f"{r.name} ({r.start}-{r.end})" for r in allowed)
        out.append(Finding(vlan, d, row.ip, f"category '{d.category}' expects: {names}"))
    return out


def check_unplanned(
    vlan: Vlan, rows: list[models.DeviceRow], ranges: list[IpRange]
) -> list[Finding]:
    """Devices holding an address outside every planned range of this VLAN.

    Only addresses a device holds count (online on it, or reserved for it). The DHCP pool
    counts as a range. VLANs without any planned range are skipped: there is nothing to be
    outside of yet, and listing every device there would hide the real findings.
    """
    if not ranges:
        return []
    out: list[Finding] = []
    for row in rows:
        for ip in sorted(row.device.claims):
            if ip not in vlan.net or ip == vlan.gateway:
                continue
            if any(r.start <= ip <= r.end for r in ranges):
                continue
            if vlan.dhcp and vlan.dhcp[0] <= ip <= vlan.dhcp[1]:
                continue
            out.append(Finding(vlan, row.device, ip, ""))
    return out


def check_conflicts(vlan: Vlan, rows: list[models.DeviceRow]) -> list[Conflict]:
    """Addresses claimed by two or more devices in this VLAN.

    A device claims an address when it is online on it, or holds a fixed reservation for it.
    Old leases of offline devices are ignored: DHCP reuses those addresses by design.
    """
    by_ip: dict[Addr, list[Device]] = {}
    for row in rows:
        for ip in row.device.claims:
            if ip in vlan.net:
                by_ip.setdefault(ip, []).append(row.device)
    return [Conflict(vlan, ip, devs) for ip, devs in sorted(by_ip.items()) if len(devs) > 1]


def check_stale_fixed(
    vlan: Vlan, rows: list[models.DeviceRow], stale_days: int, now: int
) -> list[Finding]:
    """Fixed-IP device not seen for more than stale_days."""
    limit = now - stale_days * 86400
    out: list[Finding] = []
    for row in rows:
        d = row.device
        if d.fixed_ip is None or d.online:
            continue
        if d.last_seen is None or d.last_seen < limit:
            days = "never seen" if d.last_seen is None else f"{(now - d.last_seen) // 86400}d ago"
            out.append(Finding(vlan, d, d.fixed_ip, f"fixed IP, last seen {days}"))
    return out


def load_ignored(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in conn.execute("SELECT kind, subject FROM check_ignore")}


def set_ignored(conn: sqlite3.Connection, kind: str, subject: str, ignore: bool) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown check kind {kind!r}")
    with conn:
        if ignore:
            conn.execute(
                "INSERT OR IGNORE INTO check_ignore (kind, subject, created) VALUES (?, ?, ?)",
                (kind, subject, int(time.time())),
            )
        else:
            conn.execute("DELETE FROM check_ignore WHERE kind = ? AND subject = ?", (kind, subject))


def run_checks(conn: sqlite3.Connection, stale_days: int, now: int | None = None) -> CheckReport:
    now = int(time.time()) if now is None else now
    devices = store.list_devices(conn)
    all_ranges = store.list_ranges(conn)
    report = CheckReport(ignored=load_ignored(conn))
    stale_seen: set[str] = set()  # a device can appear in two VLANs; report it once
    for vlan in store.list_vlans(conn):
        rows = views.devices_in_vlan(vlan, devices)
        ranges = [r for r in all_ranges if r.vlan_id == vlan.id]
        report.out_of_range += check_out_of_range(vlan, rows, ranges)
        report.unplanned += check_unplanned(vlan, rows, ranges)
        report.conflicts += check_conflicts(vlan, rows)
        for f in check_stale_fixed(vlan, rows, stale_days, now):
            if f.device.mac not in stale_seen and f.ip in vlan.net:
                stale_seen.add(f.device.mac)
                report.stale_fixed.append(f)
    return report
