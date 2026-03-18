"""
crowding_check.py — Component crowding checker for dpcb_api.

For each component, finds the nearest foreign-net track segment and
reports the clearance distance. Results are sorted closest-first so
the most crowded components appear at the top.

Usage:
    from crowding_check import check_crowding, format_crowding

    results = check_crowding(board)
    print(format_crowding(results))
"""

import math
from dataclasses import dataclass
from typing import Optional


DEFAULT_CLEARANCE_THRESHOLD_MM = 1.0


@dataclass
class ComponentCrowding:
    ref: str
    x: float
    y: float
    min_clearance: float
    nearest_net: Optional[str]
    crowded: bool


def check_crowding(board, threshold_mm=DEFAULT_CLEARANCE_THRESHOLD_MM):
    net_own = {}
    for net in board.nets:
        for ref, pin in net.pads:
            net_own.setdefault(net.name, set()).add(ref)

    results = []
    for fp in board.footprints:
        if not fp.abs_pads:
            continue

        pad_xs = [p.x for p in fp.abs_pads]
        pad_ys = [p.y for p in fp.abs_pads]
        cx = sum(pad_xs) / len(pad_xs)
        cy = sum(pad_ys) / len(pad_ys)
        half_w = (max(pad_xs) - min(pad_xs)) / 2 + 0.5
        half_h = (max(pad_ys) - min(pad_ys)) / 2 + 0.5

        min_clearance = float('inf')
        nearest_net = None

        for t in board.tracks:
            if fp.ref in net_own.get(t.net, set()):
                continue
            d = _dist_segment_to_box(t.x1, t.y1, t.x2, t.y2,
                                     cx - half_w, cx + half_w,
                                     cy - half_h, cy + half_h)
            if d < min_clearance:
                min_clearance = d
                nearest_net = t.net

        if min_clearance == float('inf'):
            min_clearance = None

        crowded = min_clearance is not None and min_clearance < threshold_mm

        results.append(ComponentCrowding(
            ref=fp.ref,
            x=fp.x,
            y=fp.y,
            min_clearance=round(min_clearance, 2) if min_clearance is not None else None,
            nearest_net=nearest_net,
            crowded=crowded,
        ))

    results.sort(key=lambda r: r.min_clearance if r.min_clearance is not None else float('inf'))
    return results


def _dist_segment_to_box(x1, y1, x2, y2, bx_min, bx_max, by_min, by_max):
    if _segment_intersects_box(x1, y1, x2, y2, bx_min, bx_max, by_min, by_max):
        return 0.0

    corners = [
        (bx_min, by_min), (bx_max, by_min),
        (bx_min, by_max), (bx_max, by_max),
    ]
    min_d = min(_dist_point_to_segment(px, py, x1, y1, x2, y2)
                for px, py in corners)

    edges = [
        (bx_min, by_min, bx_max, by_min),
        (bx_max, by_min, bx_max, by_max),
        (bx_max, by_max, bx_min, by_max),
        (bx_min, by_max, bx_min, by_min),
    ]
    for ex1, ey1, ex2, ey2 in edges:
        min_d = min(min_d, _dist_segment_to_segment(x1, y1, x2, y2, ex1, ey1, ex2, ey2))

    return min_d


def _dist_point_to_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.sqrt((px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2)


def _dist_segment_to_segment(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    return min(
        _dist_point_to_segment(ax1, ay1, bx1, by1, bx2, by2),
        _dist_point_to_segment(ax2, ay2, bx1, by1, bx2, by2),
        _dist_point_to_segment(bx1, by1, ax1, ay1, ax2, ay2),
        _dist_point_to_segment(bx2, by2, ax1, ay1, ax2, ay2),
    )


def _segment_intersects_box(x1, y1, x2, y2, x_min, x_max, y_min, y_max):
    if (min(x1, x2) > x_max or max(x1, x2) < x_min or
            min(y1, y2) > y_max or max(y1, y2) < y_min):
        return False
    dx, dy = x2 - x1, y2 - y1
    t_min, t_max = 0.0, 1.0
    for d, p, lo, hi in [(dx, x1, x_min, x_max), (dy, y1, y_min, y_max)]:
        if abs(d) < 1e-9:
            if p < lo or p > hi:
                return False
        else:
            t1, t2 = (lo - p) / d, (hi - p) / d
            if t1 > t2:
                t1, t2 = t2, t1
            t_min, t_max = max(t_min, t1), min(t_max, t2)
            if t_min > t_max:
                return False
    return True


def format_crowding(results, threshold_mm=DEFAULT_CLEARANCE_THRESHOLD_MM):
    if not results:
        return "OK: no components on board"

    crowded = [r for r in results if r.crowded]
    lines = [f"OK: {len(results)} component(s) checked, {len(crowded)} crowded"
             f"  (threshold={threshold_mm}mm)"]

    for r in results:
        if r.min_clearance is None:
            continue
        flag = "CROWDED" if r.crowded else "ok    "
        lines.append(f"  {flag}  {r.ref:4s} ({r.x},{r.y})"
                     f"  clearance={r.min_clearance}mm  nearest={r.nearest_net}")

    return "\n".join(lines)
