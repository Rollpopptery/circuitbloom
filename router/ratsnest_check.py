"""
ratsnest_check.py — Ratsnest crossing checker for dpcb_api.

For each net, builds MST between pads (ratsnest).
Then checks if any foreign-net pads sit close to those lines.
Reports blockages sorted worst-first.

Usage:
    from ratsnest_check import check_ratsnest, format_ratsnest

    results = check_ratsnest(board)
    print(format_ratsnest(results))
"""

import math
from mst import mst_edges
from dataclasses import dataclass
from typing import Optional


DEFAULT_RATSNEST_THRESHOLD_MM = 2.0


@dataclass
class RatsnestBlockage:
    net: str
    pad_a: str       # e.g. "U1.5"
    pad_a_xy: tuple
    pad_b: str       # e.g. "U2.1"
    pad_b_xy: tuple
    blocker_pad: str  # e.g. "R1.1"
    blocker_net: str
    blocker_xy: tuple
    distance: float   # mm from blocker to ratsnest line


def _dist_point_to_segment(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.sqrt((px - x1) ** 2 + (py - y1) ** 2)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.sqrt((px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2)



def check_ratsnest(board, threshold_mm=DEFAULT_RATSNEST_THRESHOLD_MM):
    # Build pad net map
    pad_net = {}
    for net in board.nets:
        for ref, pin in net.pads:
            pad_net[(ref, pin)] = net.name

    # Collect pads per net
    net_pads = {}
    for fp in board.footprints:
        for pad in fp.abs_pads:
            net_name = pad_net.get((fp.ref, pad.num), "")
            if net_name:
                net_pads.setdefault(net_name, []).append(
                    (f"{fp.ref}.{pad.num}", pad.x, pad.y))

    # Collect all pads flat for blocker checking
    all_pads = []
    for fp in board.footprints:
        for pad in fp.abs_pads:
            net_name = pad_net.get((fp.ref, pad.num), "")
            all_pads.append((f"{fp.ref}.{pad.num}", fp.ref, pad.x, pad.y, net_name))

    results = []

    for net_name, pads in net_pads.items():
        if len(pads) < 2:
            continue
        edges = mst_edges(pads)

        for (ref_a, ax, ay), (ref_b, bx, by) in edges:
            # Find ALL blockers for this edge
            for pad_ref, comp_ref, px, py, p_net in all_pads:
                if p_net == net_name:
                    continue  # same net, not a blocker
                d = _dist_point_to_segment(px, py, ax, ay, bx, by)
                if d < threshold_mm:
                    results.append(RatsnestBlockage(
                        net=net_name,
                        pad_a=ref_a,
                        pad_a_xy=(round(ax, 2), round(ay, 2)),
                        pad_b=ref_b,
                        pad_b_xy=(round(bx, 2), round(by, 2)),
                        blocker_pad=pad_ref,
                        blocker_net=p_net,
                        blocker_xy=(round(px, 2), round(py, 2)),
                        distance=round(d, 2),
                    ))

    results.sort(key=lambda r: r.distance)
    return results


def format_ratsnest(results, threshold_mm=DEFAULT_RATSNEST_THRESHOLD_MM):
    if not results:
        return f"OK: no ratsnest blockages (threshold={threshold_mm}mm)"

    lines = [f"OK: {len(results)} ratsnest blockage(s)  (threshold={threshold_mm}mm)"]
    for r in results:
        lines.append(
            f"  BLOCKED  {r.net}  {r.pad_a}->{r.pad_b}"
            f"  by {r.blocker_pad} ({r.blocker_net}) dist={r.distance}mm")

    return "\n".join(lines)
