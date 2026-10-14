from __future__ import annotations

import re
from ipaddress import IPv4Address as A

import pytest

from app import store, views
from app.strip import CELL_GAP, GROUP_GAP, strip_style


def _vid(conn, name):
    return next(v for v in store.list_vlans(conn) if v.name == name).id


def test_separators_sit_on_multiples_of_32(conn):
    cells = views.vlan_detail(conn, _vid(conn, "Devices")).cells
    starts = [str(c.ip) for i, c in enumerate(cells) if i and c.group_start]
    assert starts == [f"10.1.20.{n}" for n in (32, 64, 96, 128, 160, 192, 224)]


def test_page_puts_separator_before_32_not_33(client, conn):
    page = client.get(f"/vlan/{_vid(conn, 'Devices')}").text
    strip = page.split('<div class="strip">')[1].split("</div>")[0]
    cells = re.findall(r'<i class="c ([^"]*)" title="([^ "]+)', strip)
    grp = [ip for cls, ip in cells if "grp" in cls.split()]
    assert grp[0] == "10.1.20.32" and "10.1.20.33" not in grp and len(grp) == 7


def _layout(width: float, lo: int, hi: int, group_gap: int) -> dict[int, tuple[float, float]]:
    """Model the flex row exactly as the browser lays it out: x positions per address."""
    n = hi - lo + 1
    seps = hi // 32 - lo // 32
    cell = (width - CELL_GAP * (n - 1) - group_gap * seps) / n
    out, x = {}, 0.0
    for ip in range(lo, hi + 1):
        if ip != lo and ip % 32 == 0:
            x += group_gap
        out[ip] = (x, x + cell)
        x += cell + CELL_GAP
    return out


def _eval(expr: str, width: float) -> float:
    m = re.fullmatch(r"calc\(\(100% ([+-]) (\d+)px\) \* ([\d.]+) ([+-]) (\d+)px\)", expr)
    assert m, expr
    pitch = width + (int(m[2]) if m[1] == "+" else -int(m[2]))
    return pitch * float(m[3]) + (int(m[5]) if m[4] == "+" else -int(m[5]))


@pytest.mark.parametrize("width", [640.0, 1003.0, 1600.0])
@pytest.mark.parametrize(
    "start,end", [(1, 15), (16, 23), (32, 39), (40, 47), (48, 79), (104, 111), (232, 235), (1, 254)]
)
def test_labels_line_up_with_their_cells(width, start, end):
    lo, hi = 1, 254
    style = strip_style(
        A(f"10.0.0.{lo}"), A(f"10.0.0.{hi}"), A(f"10.0.0.{start}"), A(f"10.0.0.{end}")
    )
    left_expr, width_expr = re.fullmatch(r"left: (.*); width: (.*)", style).groups()
    cells = _layout(width, lo, hi, GROUP_GAP)
    left = _eval(left_expr, width)
    right = left + _eval(width_expr, width)
    assert left == pytest.approx(cells[start][0], abs=0.01)
    assert right == pytest.approx(cells[end][1], abs=0.01)


def test_zoom_window_lines_up_on_minimap(conn):
    vid = _vid(conn, "Video")
    view = views.vlan_detail(conn, vid, "dhcp")
    left_expr, width_expr = re.fullmatch(
        r"left: (.*); width: (.*)", view.zoom.window_style
    ).groups()
    cells = _layout(900.0, 1, 254, 2)
    assert _eval(left_expr, 900.0) == pytest.approx(cells[232][0], abs=0.01)
    assert _eval(left_expr, 900.0) + _eval(width_expr, 900.0) == pytest.approx(
        cells[235][1], abs=0.01
    )
