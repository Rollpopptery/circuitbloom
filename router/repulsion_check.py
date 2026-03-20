"""
repulsion_check.py — Ratsnest repulsion force for dpcb_api.

For a given component, computes a repulsion vector from nearby foreign
ratsnest lines. Each foreign ratsnest line within threshold pushes the
component perpendicular to the line, away from it. Closer lines push
harder (inverse distance).

Usage:
    from repulsion_check import compute_repulsion, compute_repulsion_all
    from repulsion_check import format_repulsion, format_repulsion_all

    result = compute_repulsion(board, "D3")
    print(format_repulsion(result))
"""

import math
from dataclasses import dataclass, field
from typing import Optional
from mst import mst_edges


DEFAULT_REPULSION_THRESHOLD_MM = 3.0


@dataclass
class NetRepulsion:
    net: str
    fx: float
    fy: float
    line_count: int


@dataclass
class RepulsionResult:
    ref: str
    x: float
    y: float
    fx: float
    fy: float
    magnitude: float
    target_x: float
    target_y: float
    line_count: int
    net_repulsions: list = field(default_factory=list)



def _point_to_segment_vector(px, py, x1, y1, x2, y2):
    """Return (distance, push_x, push_y) — unit vector pushing point away from segment."""
    dx, dy = x2 - x1, y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        # Degenerate segment
        vx, vy = px - x1, py - y1
        d = math.sqrt(vx * vx + vy * vy)
        if d < 1e-12:
            return (0.0, 0.0, 0.0)
        return (d, vx / d, vy / d)

    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    vx = px - closest_x
    vy = py - closest_y
    d = math.sqrt(vx * vx + vy * vy)
    if d < 1e-12:
        # Point is on the segment — push perpendicular
        # Perpendicular to segment direction
        perp_x = -dy
        perp_y = dx
        perp_len = math.sqrt(perp_x * perp_x + perp_y * perp_y)
        if perp_len < 1e-12:
            return (0.0, 0.0, 0.0)
        return (0.0, perp_x / perp_len, perp_y / perp_len)

    return (d, vx / d, vy / d)


def compute_repulsion(board, ref, threshold_mm=DEFAULT_REPULSION_THRESHOLD_MM):
    # Find the footprint
    fp = None
    for f in board.footprints:
        if f.ref == ref:
            fp = f
            break
    if fp is None:
        return None

    # Build pad net map
    pad_net = {}
    for net in board.nets:
        for r, pin in net.pads:
            pad_net[(r, pin)] = net.name

    # Component's own nets
    own_nets = set()
    for pad in fp.abs_pads:
        n = pad_net.get((fp.ref, pad.num), "")
        if n:
            own_nets.add(n)

    # Component center (average of pad positions)
    if not fp.abs_pads:
        return None
    cx = sum(p.x for p in fp.abs_pads) / len(fp.abs_pads)
    cy = sum(p.y for p in fp.abs_pads) / len(fp.abs_pads)

    # Build ratsnest edges for all foreign nets
    net_pads = {}
    for f in board.footprints:
        for pad in f.abs_pads:
            net_name = pad_net.get((f.ref, pad.num), "")
            if net_name:
                net_pads.setdefault(net_name, []).append(
                    (f"{f.ref}.{pad.num}", pad.x, pad.y))

    total_fx = 0.0
    total_fy = 0.0
    total_lines = 0
    net_totals = {}

    for net_name, pads in net_pads.items():
        if net_name in own_nets:
            continue  # skip own nets
        if len(pads) < 2:
            continue

        edges = mst_edges(pads)

        for (_, x1, y1), (_, x2, y2) in edges:
            dist, push_x, push_y = _point_to_segment_vector(cx, cy, x1, y1, x2, y2)

            if dist > threshold_mm:
                continue

            # Inverse distance weighting — closer = stronger push
            if dist < 0.1:
                weight = 10.0  # cap for very close / on-line
            else:
                weight = 1.0 / dist

            fx = push_x * weight
            fy = push_y * weight

            total_fx += fx
            total_fy += fy
            total_lines += 1

            if net_name not in net_totals:
                net_totals[net_name] = [0.0, 0.0, 0]
            net_totals[net_name][0] += fx
            net_totals[net_name][1] += fy
            net_totals[net_name][2] += 1

    magnitude = math.sqrt(total_fx * total_fx + total_fy * total_fy)

    net_repulsions = []
    for net_name, (nfx, nfy, count) in sorted(net_totals.items(),
                                                key=lambda x: -(x[1][0]**2 + x[1][1]**2)):
        net_repulsions.append(NetRepulsion(
            net=net_name,
            fx=round(nfx, 2),
            fy=round(nfy, 2),
            line_count=count,
        ))

    return RepulsionResult(
        ref=fp.ref,
        x=round(cx, 2),
        y=round(cy, 2),
        fx=round(total_fx, 2),
        fy=round(total_fy, 2),
        magnitude=round(magnitude, 2),
        target_x=round(cx + total_fx, 2),
        target_y=round(cy + total_fy, 2),
        line_count=total_lines,
        net_repulsions=net_repulsions,
    )


def compute_repulsion_all(board, threshold_mm=DEFAULT_REPULSION_THRESHOLD_MM):
    results = []
    for fp in board.footprints:
        r = compute_repulsion(board, fp.ref, threshold_mm=threshold_mm)
        if r:
            results.append(r)
    results.sort(key=lambda r: -r.magnitude)
    return results


def format_repulsion(result):
    if result is None:
        return "ERR: component not found"

    lines = [
        f"OK: {result.ref} ({result.x},{result.y})"
        f"  repulsion=({result.fx},{result.fy})"
        f"  mag={result.magnitude}mm"
        f"  push_toward=({result.target_x},{result.target_y})"
        f"  from {result.line_count} line(s)"
    ]
    for nr in result.net_repulsions:
        lines.append(
            f"  {nr.net}: ({nr.fx},{nr.fy}) from {nr.line_count} line(s)")

    return "\n".join(lines)


def format_repulsion_all(results):
    if not results:
        return "OK: no components"

    lines = [f"OK: {len(results)} component(s)"]
    for r in results:
        lines.append(
            f"  {r.ref:4s} ({r.x},{r.y})"
            f"  repulsion=({r.fx},{r.fy})"
            f"  mag={r.magnitude}mm"
            f"  from {r.line_count} line(s)")

    return "\n".join(lines)
