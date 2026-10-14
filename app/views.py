"""What a VLAN page shows: device placement, range stats, zoom and grouping."""

from __future__ import annotations

import sqlite3

from app import ipmath, store, strip
from app.ipmath import Addr
from app.models import (
    Cell,
    Device,
    DeviceGroup,
    DeviceRow,
    IpRange,
    RangeStats,
    Vlan,
    VlanView,
    Zoom,
)


def devices_in_vlan(vlan: Vlan, devices: list[Device]) -> list[DeviceRow]:
    """A device belongs to a VLAN if UniFi says so or if one of its IPs is in the subnet."""
    out: list[DeviceRow] = []
    for d in devices:
        in_subnet = [
            a for a in (d.address, d.fixed_ip, d.last_ip) if a is not None and a in vlan.net
        ]
        if d.vlan_unifi_id != vlan.unifi_id and not in_subnet:
            continue
        ip = in_subnet[0] if in_subnet else None
        out.append(DeviceRow(device=d, ip=ip, range_name=None))
    return sorted(out, key=lambda r: (r.ip is None, r.ip or Addr(0), r.device.display_name))


def used_ips(vlan: Vlan, devices: list[Device]) -> set[Addr]:
    ips = {a for d in devices for a in d.ips if a in vlan.net}
    ips.add(vlan.gateway)
    return ips


def range_stats(start: Addr, end: Addr, taken: set[Addr]) -> RangeStats:
    used = sum(1 for a in taken if start <= a <= end)
    return RangeStats(
        used, ipmath.range_size(start, end) - used, ipmath.next_free(start, end, taken)
    )


def vlan_overview(conn: sqlite3.Connection) -> list[VlanView]:
    devices = store.list_devices(conn)
    all_ranges = store.list_ranges(conn)
    out: list[VlanView] = []
    for v in store.list_vlans(conn):
        ranges = [r for r in all_ranges if r.vlan_id == v.id]
        util = ipmath.utilisation(v.net, used_ips(v, devices), [(r.start, r.end) for r in ranges])
        out.append(VlanView(vlan=v, util=util, ranges=ranges))
    return out


def out_of_range_ips(rows: list[DeviceRow], ranges: list[IpRange]) -> set[Addr]:
    """IPs of devices whose category has ranges in this VLAN but which sit outside them."""
    by_cat: dict[str, list[IpRange]] = {}
    for r in ranges:
        if r.category:
            by_cat.setdefault(r.category, []).append(r)
    bad: set[Addr] = set()
    for row in rows:
        cat = row.device.category
        if row.ip is None or cat not in by_cat:
            continue
        if not any(r.start <= row.ip <= r.end for r in by_cat[cat]):
            bad.add(row.ip)
    return bad


def _range_for(ip: Addr | None, ranges: list[IpRange]) -> IpRange | None:
    if ip is None:
        return None
    return next((r for r in ranges if r.start <= ip <= r.end), None)


def _row(base: DeviceRow, ranges: list[IpRange]) -> DeviceRow:
    rng = _range_for(base.ip, ranges)
    inherited = rng.category if rng and rng.category and not base.device.category else None
    return DeviceRow(base.device, base.ip, rng.name if rng else None, inherited)


def _zoom(
    v: Vlan,
    key: str,
    ranges: list[IpRange],
    hues: dict[int, int],
    rows: list[DeviceRow],
    used: set[Addr],
) -> Zoom | None:
    if key == "dhcp" and v.dhcp:
        name, start, end, hue = "DHCP pool", v.dhcp[0], v.dhcp[1], None
    else:
        rng = next((r for r in ranges if str(r.id) == key), None)
        if rng is None:
            return None
        name, start, end, hue = rng.name, rng.start, rng.end, hues[rng.id]
    lo, hi = ipmath.host_bounds(v.net)
    start, end = max(start, lo), min(end, hi)
    if start > end:
        return None
    return Zoom(
        key=key,
        name=name,
        start=start,
        end=end,
        hue=hue,
        size=ipmath.range_size(start, end),
        used=sum(1 for r in rows if r.ip is not None and start <= r.ip <= end),
        next_free=ipmath.next_free(start, end, used) if hue is not None else None,
        window_style=strip.strip_style(lo, hi, start, end, strip.MINI_GROUP_GAP),
    )


def vlan_detail(conn: sqlite3.Connection, vlan_id: int, zoom: str | None = None) -> VlanView | None:
    v = store.get_vlan(conn, vlan_id)
    if v is None:
        return None
    devices = store.list_devices(conn)
    ranges = store.list_ranges(conn, v.id)
    used = used_ips(v, devices)
    util = ipmath.utilisation(v.net, used, [(r.start, r.end) for r in ranges])
    rows = [_row(r, ranges) for r in devices_in_vlan(v, devices)]
    bad = out_of_range_ips(rows, ranges)
    hues = strip.range_hues(ranges)
    dhcp_size = ipmath.range_size(*v.dhcp) if v.dhcp else 0
    cells = strip.build_cells(v, rows, ranges, bad, hues)
    z = _zoom(v, zoom, ranges, hues, rows, used) if zoom else None
    if z:
        at: dict[Addr, list[DeviceRow]] = {}
        for r in rows:
            if r.ip is not None:
                at.setdefault(r.ip, []).append(r)
        zoom_cells = [
            Cell(c.ip, c.kind, strip.zoom_title(c, at.get(c.ip, [])), c.hue)
            for c in cells
            if z.start <= c.ip <= z.end
        ]
        shown = [r for r in rows if r.ip is not None and z.start <= r.ip <= z.end]
    else:
        zoom_cells, shown = [], rows
    return VlanView(
        vlan=v,
        util=util,
        ranges=ranges,
        devices=rows,
        cells=cells,
        marks=strip.build_marks(v, ranges, hues),
        hues=hues,
        bad_ips=bad,
        dhcp_size=dhcp_size,
        zoom=z,
        stats={r.id: range_stats(r.start, r.end, used) for r in ranges},
        dhcp_stats=range_stats(v.dhcp[0], v.dhcp[1], used) if v.dhcp else None,
        zoom_cells=zoom_cells,
        shown=shown,
    )


def next_free(conn: sqlite3.Connection, vlan_id: int, range_name: str) -> Addr | None:
    v = store.get_vlan(conn, vlan_id)
    if v is None:
        raise LookupError("vlan not found")
    rng = next((r for r in store.list_ranges(conn, v.id) if r.name == range_name), None)
    if rng is None:
        raise LookupError("range not found")
    return ipmath.next_free(rng.start, rng.end, used_ips(v, store.list_devices(conn)))


def device_groups(view: VlanView) -> list[DeviceGroup]:
    """Consecutive devices (sorted by address) grouped by the range they sit in."""
    by_name = {r.name: r for r in view.ranges}
    dhcp = view.vlan.dhcp
    groups: list[DeviceGroup] = []
    for row in view.shown:
        rng = by_name.get(row.range_name) if row.range_name else None
        in_pool = dhcp is not None and row.ip is not None and dhcp[0] <= row.ip <= dhcp[1]
        kind = "range" if rng else "dhcp" if in_pool else "none"
        last = groups[-1] if groups else None
        if last and last.kind == kind and last.range is rng:
            last.rows.append(row)
        elif rng:
            groups.append(
                DeviceGroup(
                    "range",
                    rng,
                    view.hues.get(rng.id),
                    [row],
                    view.stats.get(rng.id),
                    rng.start,
                    rng.end,
                )
            )
        elif in_pool:
            groups.append(DeviceGroup("dhcp", None, None, [row], view.dhcp_stats, dhcp[0], dhcp[1]))
        else:
            groups.append(DeviceGroup("none", None, None, [row], None))
    return groups
