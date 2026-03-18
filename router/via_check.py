"""
via_check.py — Via proximity checker for dpcb_api.

Checks each placed via against all nearby entities on the board:
  1. Pads (all nets)
  2. Foreign-net track segments
  3. Foreign-net vias

Reports vias that fall within a clearance threshold. Used to identify
vias placed too close to other entities by the auto-router, which can
then be manually relocated using the via + re-route workflow.

Usage:
    from via_check import check_vias, format_viacheck

    results = check_vias(board, threshold_mm=2.0)
    print(format_viacheck(results, threshold_mm=2.0))
"""

import math
from dataclasses import dataclass
from typing import Optional


DEFAULT_THRESHOLD_MM = 2.0


@dataclass
class Proximity:
    """Nearest entity to a via."""
    label: str       # e.g. "U1.3", "trk:/oe", "via:/clk"
    x: float
    y: float
    distance: float


@dataclass
class ViaCheckResult:
    """Result for a single via."""
    via_x: float
    via_y: float
    via_net: str
    nearest: Optional[Proximity]
    passed: bool


def _point_to_segment_dist(px, py, x1, y1, x2, y2):
    """Minimum distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq == 0:
        return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)


def check_vias(board, threshold_mm=DEFAULT_THRESHOLD_MM):
    """
    Check all vias on the board for proximity to pads, tracks, and other vias.

    Returns list of ViaCheckResult, one per via.
    """
    # Collect all pad positions
    pads = []
    for fp in board.footprints:
        for pad in fp.abs_pads:
            pads.append((f"{fp.ref}.{pad.num}", pad.x, pad.y))

    results = []
    for via in board.vias:
        nearest = None
        best_dist = float('inf')

        # Check against all pads
        for label, px, py in pads:
            d = math.sqrt((via.x - px) ** 2 + (via.y - py) ** 2)
            if d < best_dist:
                best_dist = d
                nearest = Proximity(label=label, x=px, y=py, distance=d)

        # Check against foreign-net track segments
        for trk in board.tracks:
            if trk.net == via.net:
                continue
            d = _point_to_segment_dist(via.x, via.y, trk.x1, trk.y1, trk.x2, trk.y2)
            if d < best_dist:
                best_dist = d
                # Use midpoint of segment for display coords
                mx = (trk.x1 + trk.x2) / 2
                my = (trk.y1 + trk.y2) / 2
                nearest = Proximity(label=f"trk:{trk.net}", x=mx, y=my, distance=d)

        # Check against foreign-net vias
        for other in board.vias:
            if other is via or other.net == via.net:
                continue
            d = math.sqrt((via.x - other.x) ** 2 + (via.y - other.y) ** 2)
            if d < best_dist:
                best_dist = d
                nearest = Proximity(label=f"via:{other.net}", x=other.x, y=other.y, distance=d)

        passed = nearest is None or nearest.distance >= threshold_mm
        results.append(ViaCheckResult(
            via_x=via.x,
            via_y=via.y,
            via_net=via.net,
            nearest=nearest,
            passed=passed,
        ))

    return results


def format_viacheck(results, threshold_mm=DEFAULT_THRESHOLD_MM):
    """Format check results as a human/AI-readable string."""
    if not results:
        return "OK: no vias on board"

    lines = [f"OK: {len(results)} via(s) checked, threshold={threshold_mm}mm"]
    fails = 0
    for r in results:
        status = "OK  " if r.passed else "FAIL"
        if r.nearest:
            lines.append(
                f"  {status} ({r.via_x},{r.via_y}) {r.via_net}"
                f"  nearest={r.nearest.label}"
                f" @ ({r.nearest.x},{r.nearest.y})"
                f"  dist={r.nearest.distance:.2f}mm"
            )
        else:
            lines.append(f"  {status} ({r.via_x},{r.via_y}) {r.via_net}  no entities")
        if not r.passed:
            fails += 1

    if fails:
        lines.append(f"  {fails} via(s) within threshold — relocate using: unroute / via / re-route")
    else:
        lines.append(f"  all vias clear")

    return "\n".join(lines)
