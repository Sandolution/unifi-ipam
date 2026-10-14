"""All IPv4 arithmetic. Pure functions on stdlib ipaddress types."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network

Addr = IPv4Address


def parse_network(text: str) -> IPv4Network:
    """Accepts '10.1.10.0/24' or the UniFi gateway form '10.1.10.1/24'."""
    net = ip_network(text.strip(), strict=False)
    if net.version != 4:
        raise ValueError("IPv4 only")
    return net


def parse_addr(text: str) -> Addr:
    a = ip_address(text.strip())
    if a.version != 4:
        raise ValueError("IPv4 only")
    return a


def host_bounds(net: IPv4Network) -> tuple[Addr, Addr]:
    """First and last assignable address (excludes network/broadcast for prefix <= 30)."""
    if net.prefixlen >= 31:
        return net[0], net[-1]
    return net[1], net[-2]


def host_count(net: IPv4Network) -> int:
    first, last = host_bounds(net)
    return int(last) - int(first) + 1


def range_size(start: Addr, end: Addr) -> int:
    return int(end) - int(start) + 1


def overlaps(a_start: Addr, a_end: Addr, b_start: Addr, b_end: Addr) -> bool:
    """Inclusive ranges; sharing a single address counts as overlap."""
    return a_start <= b_end and b_start <= a_end


@dataclass(frozen=True)
class RangeCheck:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def check_range(
    net: IPv4Network,
    start: Addr,
    end: Addr,
    others: list[tuple[str, Addr, Addr]],
    dhcp: tuple[Addr, Addr] | None,
) -> RangeCheck:
    errors: list[str] = []
    warnings: list[str] = []
    lo, hi = host_bounds(net)
    if start > end:
        errors.append("start must be <= end")
    for label, a in (("start", start), ("end", end)):
        if a not in net:
            errors.append(f"{label} {a} is outside {net}")
        elif not lo <= a <= hi:
            errors.append(f"{label} {a} is the network or broadcast address")
    for name, o_start, o_end in others:
        if overlaps(start, end, o_start, o_end):
            errors.append(f"overlaps range '{name}' ({o_start}-{o_end})")
    if dhcp and overlaps(start, end, dhcp[0], dhcp[1]):
        warnings.append(f"overlaps the DHCP pool ({dhcp[0]}-{dhcp[1]})")
    return RangeCheck(errors, warnings)


@dataclass(frozen=True)
class Utilisation:
    total: int
    used: int
    reserved: int  # inside a range but not used

    @property
    def free(self) -> int:
        return self.total - self.used - self.reserved

    def pct(self, n: int) -> float:
        return 0.0 if self.total == 0 else round(100.0 * n / self.total, 2)


def utilisation(
    net: IPv4Network, used_ips: set[Addr], ranges: list[tuple[Addr, Addr]]
) -> Utilisation:
    lo, hi = host_bounds(net)
    used = {a for a in used_ips if lo <= a <= hi}
    reserved = 0
    for r_start, r_end in ranges:
        s, e = max(r_start, lo), min(r_end, hi)
        if s > e:
            continue
        in_range_used = sum(1 for a in used if s <= a <= e)
        reserved += range_size(s, e) - in_range_used
    return Utilisation(total=host_count(net), used=len(used), reserved=reserved)


def next_free(start: Addr, end: Addr, used_ips: set[Addr]) -> Addr | None:
    cur, last = int(start), int(end)
    while cur <= last:
        a = Addr(cur)
        if a not in used_ips:
            return a
        cur += 1
    return None
