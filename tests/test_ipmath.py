from __future__ import annotations

from ipaddress import IPv4Address as A
from ipaddress import IPv4Network as N

import pytest

from app import ipmath


def test_parse_network_accepts_gateway_form():
    assert ipmath.parse_network("10.1.10.1/24") == N("10.1.10.0/24")


def test_host_bounds_and_count():
    assert ipmath.host_bounds(N("10.0.0.0/24")) == (A("10.0.0.1"), A("10.0.0.254"))
    assert ipmath.host_count(N("10.0.0.0/24")) == 254
    assert ipmath.host_bounds(N("10.0.0.0/31")) == (A("10.0.0.0"), A("10.0.0.1"))
    assert ipmath.host_count(N("10.0.0.0/32")) == 1


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (("10.0.0.10", "10.0.0.20"), ("10.0.0.21", "10.0.0.30"), False),  # adjacent
        (("10.0.0.10", "10.0.0.20"), ("10.0.0.20", "10.0.0.30"), True),  # touch at one address
        (("10.0.0.10", "10.0.0.20"), ("10.0.0.15", "10.0.0.15"), True),  # single inside
        (("10.0.0.10", "10.0.0.10"), ("10.0.0.10", "10.0.0.10"), True),  # identical single
        (("10.0.0.10", "10.0.0.20"), ("10.0.0.1", "10.0.0.9"), False),
        (("10.0.0.10", "10.0.0.20"), ("10.0.0.1", "10.0.0.30"), True),  # contains
    ],
)
def test_overlaps(a, b, expected):
    assert ipmath.overlaps(A(a[0]), A(a[1]), A(b[0]), A(b[1])) is expected
    assert ipmath.overlaps(A(b[0]), A(b[1]), A(a[0]), A(a[1])) is expected


def test_check_range_happy_path():
    c = ipmath.check_range(N("10.0.0.0/24"), A("10.0.0.21"), A("10.0.0.60"), [], None)
    assert c.ok and not c.warnings


def test_check_range_rejects_network_and_broadcast():
    net = N("10.0.0.0/24")
    assert (
        "network or broadcast"
        in ipmath.check_range(net, A("10.0.0.0"), A("10.0.0.5"), [], None).errors[0]
    )
    assert not ipmath.check_range(net, A("10.0.0.250"), A("10.0.0.255"), [], None).ok


def test_check_range_rejects_outside_subnet_and_reversed():
    net = N("10.0.0.0/24")
    assert "outside" in ipmath.check_range(net, A("10.0.1.5"), A("10.0.1.9"), [], None).errors[0]
    assert (
        "start must be <= end"
        in ipmath.check_range(net, A("10.0.0.9"), A("10.0.0.5"), [], None).errors
    )


def test_check_range_overlap_is_error_dhcp_is_warning():
    net = N("10.0.0.0/24")
    others = [("cams", A("10.0.0.21"), A("10.0.0.60"))]
    c = ipmath.check_range(
        net, A("10.0.0.60"), A("10.0.0.70"), others, (A("10.0.0.65"), A("10.0.0.90"))
    )
    assert c.errors == ["overlaps range 'cams' (10.0.0.21-10.0.0.60)"]
    assert c.warnings == ["overlaps the DHCP pool (10.0.0.65-10.0.0.90)"]
    c2 = ipmath.check_range(
        net, A("10.0.0.61"), A("10.0.0.64"), others, (A("10.0.0.65"), A("10.0.0.90"))
    )
    assert c2.ok and not c2.warnings


def test_single_address_range_is_valid():
    assert ipmath.check_range(N("10.0.0.0/24"), A("10.0.0.5"), A("10.0.0.5"), [], None).ok


def test_utilisation_counts():
    net = N("10.0.0.0/24")
    used = {
        A("10.0.0.1"),
        A("10.0.0.25"),
        A("10.0.0.100"),
        A("10.0.0.0"),
        A("10.0.0.255"),
        A("10.9.9.9"),
    }
    u = ipmath.utilisation(net, used, [(A("10.0.0.21"), A("10.0.0.30"))])
    assert (u.total, u.used, u.reserved, u.free) == (254, 3, 9, 242)
    assert u.pct(u.used) == pytest.approx(1.18, abs=0.01)


def test_utilisation_range_clipped_to_host_bounds():
    net = N("10.0.0.0/30")  # hosts .1 and .2
    u = ipmath.utilisation(net, set(), [(A("10.0.0.0"), A("10.0.0.3"))])
    assert (u.total, u.reserved, u.free) == (2, 2, 0)


def test_next_free_skips_used_and_handles_full_range():
    used = {A("10.0.0.21"), A("10.0.0.22")}
    assert ipmath.next_free(A("10.0.0.21"), A("10.0.0.30"), used) == A("10.0.0.23")
    assert ipmath.next_free(A("10.0.0.21"), A("10.0.0.22"), used) is None
    assert ipmath.next_free(A("10.0.0.5"), A("10.0.0.5"), set()) == A("10.0.0.5")
    assert ipmath.next_free(A("10.0.0.5"), A("10.0.0.5"), {A("10.0.0.5")}) is None
