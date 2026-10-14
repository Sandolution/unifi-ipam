"""Drawing the address strip: one cell per host, range colours, exact label positions."""

from __future__ import annotations

from app import ipmath
from app.ipmath import Addr
from app.models import GROUP, Cell, DeviceRow, IpRange, Mark, Vlan

MAX_STRIP_HOSTS = 1024  # no per-address strip for subnets larger than this


HUES = 8  # colour slots in the stylesheet (.h0 .. .h7)


def range_hues(ranges: list[IpRange]) -> dict[int, int]:
    """Stable colour slot per range: by position in the VLAN, so neighbours differ."""
    return {r.id: i % HUES for i, r in enumerate(sorted(ranges, key=lambda r: r.start))}


CELL_GAP = 1  # px between cells (flex gap in the stylesheet)


GROUP_GAP = 4  # extra px before each group on the strip (.strip .c.grp)


MINI_GROUP_GAP = 2  # same on the zoom minimap (.strip.mini .c.grp)


def _multiples(a: int, b: int) -> int:
    """Multiples of GROUP in the integer interval (a, b]: separators passed going a -> b."""
    return b // GROUP - a // GROUP


def strip_style(lo: Addr, hi: Addr, start: Addr, end: Addr, group_gap: int = GROUP_GAP) -> str:
    """CSS left/width that cover exactly the cells start..end of a strip running lo..hi.

    The strip is a flex row of n equal cells with CELL_GAP between them and group_gap extra
    before every group. A cell's pitch is therefore (W + CELL_GAP - group_gap * G) / n, where
    G is the number of separators; positions follow from that, so labels never drift.
    """
    lo_i, hi_i, s, e = int(lo), int(hi), int(start), int(end)
    n = hi_i - lo_i + 1
    base = CELL_GAP - group_gap * _multiples(lo_i, hi_i)
    pitch = f"(100% {'+' if base >= 0 else '-'} {abs(base)}px)"
    left = f"calc({pitch} * {(s - lo_i) / n:.6f} + {group_gap * _multiples(lo_i, s)}px)"
    extra = group_gap * _multiples(s, e) - CELL_GAP
    width = f"calc({pitch} * {(e - s + 1) / n:.6f} {'+' if extra >= 0 else '-'} {abs(extra)}px)"
    return f"left: {left}; width: {width}"


def build_marks(vlan: Vlan, ranges: list[IpRange], hues: dict[int, int]) -> list[Mark]:
    lo, hi = ipmath.host_bounds(vlan.net)
    spans = [(r.start, r.end, r.name, hues[r.id], str(r.id)) for r in ranges]
    if vlan.dhcp:
        spans.append((vlan.dhcp[0], vlan.dhcp[1], "DHCP pool", None, "dhcp"))
    out: list[Mark] = []
    for start, end, name, hue, key in spans:
        s, e = max(start, lo), min(end, hi)
        if s <= e:
            out.append(Mark(s, name, hue, key, strip_style(lo, hi, s, e)))
    return sorted(out, key=lambda m: m.start)


def build_cells(
    vlan: Vlan,
    rows: list[DeviceRow],
    ranges: list[IpRange],
    bad_ips: set[Addr],
    hues: dict[int, int] | None = None,
) -> list[Cell]:
    lo, hi = ipmath.host_bounds(vlan.net)
    if ipmath.host_count(vlan.net) > MAX_STRIP_HOSTS:
        return []
    names: dict[Addr, str] = {}
    for r in rows:
        if r.ip is not None:
            names.setdefault(r.ip, r.device.display_name)
    hues = hues if hues is not None else range_hues(ranges)
    cells: list[Cell] = []
    for n in range(int(lo), int(hi) + 1):
        ip = Addr(n)
        rng = next((r for r in ranges if r.start <= ip <= r.end), None)
        in_dhcp = vlan.dhcp is not None and vlan.dhcp[0] <= ip <= vlan.dhcp[1]
        if ip == vlan.gateway:
            cells.append(Cell(ip, "gateway", f"{ip} gateway"))
        elif ip in bad_ips:
            cells.append(Cell(ip, "bad", f"{ip} {names.get(ip, '')} · outside its category range"))
        elif ip in names and rng:
            cells.append(Cell(ip, "used-reserved", f"{ip} {names[ip]} · {rng.name}", hues[rng.id]))
        elif ip in names:
            cells.append(Cell(ip, "used", f"{ip} {names[ip]}"))
        elif rng:
            cells.append(Cell(ip, "reserved", f"{ip} reserved · {rng.name}", hues[rng.id]))
        elif in_dhcp:
            cells.append(Cell(ip, "dhcp", f"{ip} DHCP pool"))
        else:
            cells.append(Cell(ip, "free", f"{ip} free"))
    return cells


def zoom_title(c: Cell, here: list[DeviceRow]) -> str:
    """Tooltip for a zoomed cell: who is on this address, not which range it is in."""
    lines = [str(c.ip)]
    if c.kind == "gateway":
        lines.append("Gateway")
    for r in here:
        d = r.device
        parts = [d.display_name, d.mac]
        if d.vendor:
            parts.append(d.vendor)
        parts.append("Online" if d.online else "Offline")
        lines.append(" · ".join(parts))
    if c.kind == "bad":
        lines.append("Outside its category range")
    if not here and c.kind != "gateway":
        lines.append("DHCP pool, free" if c.kind == "dhcp" else "Free · click to copy")
    return "\n".join(lines)
