"""
pad_crowding_check.py — Pad-to-pad proximity checker for dpcb_api.

For each component, finds the nearest foreign-net pad on a different
component and reports the distance. Useful during placement to detect
overlapping or too-close pads before any routing is done.

Usage:
    from pad_crowding_check import check_pad_crowding, format_pad_crowding

    results = check_pad_crowding(board)
    print(format_pad_crowding(results))
"""

import math
from dataclasses import dataclass
from typing import Optional


DEFAULT_PAD_THRESHOLD_MM = 1.5


@dataclass
class PadCrowdingResult:
    ref: str
    x: float
    y: float
    min_distance: Optional[float]
    nearest_pad_ref: Optional[str]   # e.g. "R2.1"
    nearest_pad_net: Optional[str]
    crowded: bool


def check_pad_crowding(board, threshold_mm=DEFAULT_PAD_THRESHOLD_MM):
    # Build map: (ref, pin) -> net name
    pad_net = {}
    for net in board.nets:
        for ref, pin in net.pads:
            pad_net[(ref, pin)] = net.name

    # Collect all absolute pads with their component ref and net
    all_pads = []
    for fp in board.footprints:
        for pad in fp.abs_pads:
            net_name = pad_net.get((fp.ref, pad.num), "")
            all_pads.append((fp.ref, pad.num, pad.x, pad.y, net_name))

    # For each component, find nearest pad on a different component
    # Checks ALL pads (including same-net) — physical overlap is never OK
    results = []
    for fp in board.footprints:
        min_dist = float('inf')
        nearest_ref = None
        nearest_net = None

        for pad in fp.abs_pads:
            for other_ref, other_num, ox, oy, other_net in all_pads:
                if other_ref == fp.ref:
                    continue
                d = math.sqrt((pad.x - ox) ** 2 + (pad.y - oy) ** 2)
                if d < min_dist:
                    min_dist = d
                    nearest_ref = f"{other_ref}.{other_num}"
                    nearest_net = other_net

        if min_dist == float('inf'):
            min_dist = None

        crowded = min_dist is not None and min_dist < threshold_mm

        results.append(PadCrowdingResult(
            ref=fp.ref,
            x=fp.x,
            y=fp.y,
            min_distance=round(min_dist, 2) if min_dist is not None else None,
            nearest_pad_ref=nearest_ref,
            nearest_pad_net=nearest_net,
            crowded=crowded,
        ))

    results.sort(key=lambda r: r.min_distance if r.min_distance is not None else float('inf'))
    return results


def format_pad_crowding(results, threshold_mm=DEFAULT_PAD_THRESHOLD_MM):
    if not results:
        return "OK: no components on board"

    crowded = [r for r in results if r.crowded]
    lines = [f"OK: {len(results)} component(s) checked, {len(crowded)} crowded"
             f"  (threshold={threshold_mm}mm)"]

    for r in results:
        if r.min_distance is None:
            continue
        flag = "CROWDED" if r.crowded else "ok    "
        lines.append(f"  {flag}  {r.ref:4s} ({r.x},{r.y})"
                     f"  nearest_pad={r.min_distance}mm  to={r.nearest_pad_ref}"
                     f"  net={r.nearest_pad_net}")

    return "\n".join(lines)
