"""
pad_pressure.py — Foreign pad pressure around a component.

For a given component, computes a pressure vector from all nearby
foreign pads (within threshold), weighted by inverse distance.
Shows which direction foreign pads are concentrated — the component
could move away from pressure to balance clearance.

Usage:
    from pad_pressure import compute_pressure, format_pressure

    result = compute_pressure(board, "U2")
    print(format_pressure(result))
"""

import math
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_PRESSURE_THRESHOLD_MM = 5.0


@dataclass
class PadPressureEntry:
    pad_ref: str
    net: str
    x: float
    y: float
    distance: float
    push_x: float
    push_y: float


@dataclass
class PressureResult:
    ref: str
    cx: float
    cy: float
    fx: float
    fy: float
    magnitude: float
    pad_count: int
    pads: list = field(default_factory=list)


def compute_pressure(board, ref, threshold_mm=DEFAULT_PRESSURE_THRESHOLD_MM):
    # Find the footprint
    fp = None
    for f in board.footprints:
        if f.ref == ref:
            fp = f
            break
    if fp is None:
        return None

    # Component center (average of pad positions)
    if not fp.abs_pads:
        return None
    cx = sum(p.x for p in fp.abs_pads) / len(fp.abs_pads)
    cy = sum(p.y for p in fp.abs_pads) / len(fp.abs_pads)

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

    # Collect all foreign pads within threshold
    total_fx = 0.0
    total_fy = 0.0
    entries = []

    for f in board.footprints:
        if f.ref == fp.ref:
            continue
        for pad in f.abs_pads:
            net_name = pad_net.get((f.ref, pad.num), "")

            dx = pad.x - cx
            dy = pad.y - cy
            dist = math.sqrt(dx * dx + dy * dy)

            if dist > threshold_mm or dist < 0.01:
                continue

            # Push away from this pad — inverse distance weighting
            weight = 1.0 / dist
            push_x = -(dx / dist) * weight
            push_y = -(dy / dist) * weight

            total_fx += push_x
            total_fy += push_y

            entries.append(PadPressureEntry(
                pad_ref=f"{f.ref}.{pad.num}",
                net=net_name,
                x=round(pad.x, 2),
                y=round(pad.y, 2),
                distance=round(dist, 2),
                push_x=round(push_x, 2),
                push_y=round(push_y, 2),
            ))

    # Also include vias as pressure sources
    for v in board.vias:
        # Skip vias on component's own nets
        if v.net in own_nets:
            continue

        dx = v.x - cx
        dy = v.y - cy
        dist = math.sqrt(dx * dx + dy * dy)

        if dist > threshold_mm or dist < 0.01:
            continue

        weight = 1.0 / dist
        push_x = -(dx / dist) * weight
        push_y = -(dy / dist) * weight

        total_fx += push_x
        total_fy += push_y

        entries.append(PadPressureEntry(
            pad_ref=f"VIA:{v.net}",
            net=v.net,
            x=round(v.x, 2),
            y=round(v.y, 2),
            distance=round(dist, 2),
            push_x=round(push_x, 2),
            push_y=round(push_y, 2),
        ))

    entries.sort(key=lambda e: e.distance)

    magnitude = math.sqrt(total_fx * total_fx + total_fy * total_fy)

    return PressureResult(
        ref=fp.ref,
        cx=round(cx, 2),
        cy=round(cy, 2),
        fx=round(total_fx, 2),
        fy=round(total_fy, 2),
        magnitude=round(magnitude, 2),
        pad_count=len(entries),
        pads=entries,
    )


def compute_pressure_all(board, threshold_mm=DEFAULT_PRESSURE_THRESHOLD_MM):
    results = []
    for fp in board.footprints:
        r = compute_pressure(board, fp.ref, threshold_mm=threshold_mm)
        if r:
            results.append(r)
    results.sort(key=lambda r: -r.magnitude)
    return results


def format_pressure(result):
    if result is None:
        return "ERR: component not found"

    lines = [
        f"OK: {result.ref} center=({result.cx},{result.cy})"
        f"  pressure=({result.fx},{result.fy})"
        f"  mag={result.magnitude}mm"
        f"  from {result.pad_count} pad(s)"
    ]
    for e in result.pads:
        lines.append(
            f"  {e.pad_ref:8s} ({e.x},{e.y})  {e.net:20s}  dist={e.distance}mm"
            f"  push=({e.push_x},{e.push_y})")

    return "\n".join(lines)


def format_pressure_all(results):
    if not results:
        return "OK: no components"

    lines = [f"OK: {len(results)} component(s)"]
    for r in results:
        lines.append(
            f"  {r.ref:4s} ({r.cx},{r.cy})"
            f"  pressure=({r.fx},{r.fy})"
            f"  mag={r.magnitude}mm"
            f"  from {r.pad_count} pad(s)")

    return "\n".join(lines)
