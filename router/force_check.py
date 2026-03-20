"""
force_check.py — Force-directed placement query for dpcb_api.

For a given component, computes the net attraction vector from all
ratsnest connections. Each connected pad on another component pulls
the queried component toward it. Returns the sum force vector,
magnitude, target point, and per-net breakdown.

Usage:
    from force_check import compute_force, format_force

    result = compute_force(board, "C2")
    print(format_force(result))
"""

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NetForce:
    net: str
    fx: float
    fy: float
    pad_count: int  # number of foreign pads pulling


@dataclass
class ForceResult:
    ref: str
    x: float
    y: float
    fx: float
    fy: float
    magnitude: float
    target_x: float
    target_y: float
    net_forces: list = field(default_factory=list)


def compute_force(board, ref):
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

    # Build map: net -> list of all absolute pad positions
    net_pad_positions = {}
    for f in board.footprints:
        for pad in f.abs_pads:
            net_name = pad_net.get((f.ref, pad.num), "")
            if net_name:
                net_pad_positions.setdefault(net_name, []).append(
                    (f.ref, pad.num, pad.x, pad.y))

    # For each pad on this component, compute attraction to foreign pads
    total_fx = 0.0
    total_fy = 0.0
    net_totals = {}  # net -> (fx, fy, count)

    for pad in fp.abs_pads:
        net_name = pad_net.get((fp.ref, pad.num), "")
        if not net_name:
            continue

        for other_ref, other_num, ox, oy in net_pad_positions.get(net_name, []):
            if other_ref == fp.ref:
                continue  # skip own pads

            # Vector from this pad to the connected pad
            dx = ox - pad.x
            dy = oy - pad.y
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 0.01:
                continue  # coincident, skip

            # Normalize to unit vector — equal pull regardless of distance
            # This prevents distant pads from dominating
            ux = dx / dist
            uy = dy / dist

            total_fx += ux
            total_fy += uy

            if net_name not in net_totals:
                net_totals[net_name] = [0.0, 0.0, 0]
            net_totals[net_name][0] += ux
            net_totals[net_name][1] += uy
            net_totals[net_name][2] += 1

    magnitude = math.sqrt(total_fx * total_fx + total_fy * total_fy)

    # Target point: component position + force vector (scaled to 1mm per unit)
    target_x = fp.x + total_fx
    target_y = fp.y + total_fy

    net_forces = []
    for net_name, (nfx, nfy, count) in sorted(net_totals.items(),
                                                key=lambda x: -(x[1][0]**2 + x[1][1]**2)):
        net_forces.append(NetForce(
            net=net_name,
            fx=round(nfx, 2),
            fy=round(nfy, 2),
            pad_count=count,
        ))

    return ForceResult(
        ref=fp.ref,
        x=fp.x,
        y=fp.y,
        fx=round(total_fx, 2),
        fy=round(total_fy, 2),
        magnitude=round(magnitude, 2),
        target_x=round(target_x, 2),
        target_y=round(target_y, 2),
        net_forces=net_forces,
    )


def compute_force_all(board):
    results = []
    for fp in board.footprints:
        r = compute_force(board, fp.ref)
        if r:
            results.append(r)
    results.sort(key=lambda r: -r.magnitude)
    return results


def format_force(result):
    if result is None:
        return "ERR: component not found"

    lines = [
        f"OK: {result.ref} ({result.x},{result.y})"
        f"  force=({result.fx},{result.fy})"
        f"  mag={result.magnitude}mm"
        f"  toward=({result.target_x},{result.target_y})"
    ]
    for nf in result.net_forces:
        lines.append(
            f"  {nf.net}: ({nf.fx},{nf.fy}) from {nf.pad_count} pad(s)")

    return "\n".join(lines)


def format_force_all(results):
    if not results:
        return "OK: no components"

    lines = [f"OK: {len(results)} component(s)"]
    for r in results:
        lines.append(
            f"  {r.ref:4s} ({r.x},{r.y})"
            f"  force=({r.fx},{r.fy})"
            f"  mag={r.magnitude}mm"
            f"  toward=({r.target_x},{r.target_y})")

    return "\n".join(lines)
