"""
rotation_hint.py
Suggests rotating a footprint if another rotation reduces total wirelength.

For each footprint, tries all 4 rotations and computes the sum of distances
from each of its pads to the nearest other pad on the same net. If a
different rotation scores lower, emits a HINT.

Returns hints as strings (not hard violations — caller handles separately).
"""

import math
from .dpcb_parser import rotate_pad


def _wirelength_contribution(ref, fp, pads_lib, all_pad_positions, nets, rot_override=None):
    """
    Compute wirelength contribution of footprint `ref` at given rotation.
    For each pad on this footprint, find the nearest pad on the same net
    belonging to a different footprint. Sum those distances.
    """
    rot = rot_override if rot_override is not None else fp['rot']
    lib_fp = fp['lib']
    if lib_fp not in pads_lib:
        return 0.0

    # Build this fp's pad positions at the candidate rotation
    my_pads = {}
    for pnum, (dx, dy) in pads_lib[lib_fp].items():
        rdx, rdy = rotate_pad(dx, dy, rot)
        my_pads[pnum] = (fp['x'] + rdx, fp['y'] + rdy)

    # Build lookup: net -> list of (x, y) for pads NOT on this footprint
    net_others = {}
    for (other_ref, other_pnum), (ax, ay, net) in all_pad_positions.items():
        if other_ref == ref:
            continue
        net_others.setdefault(net, []).append((ax, ay))

    total = 0.0
    for pnum, (px, py) in my_pads.items():
        # Find which net this pad belongs to
        net = None
        for net_name, pad_list in nets.items():
            if (ref, pnum) in [(r, p) for r, p in pad_list]:
                net = net_name
                break
        if net is None or net not in net_others:
            continue
        # Manhattan distance to nearest other pad on same net
        min_d = min(abs(px - ox) + abs(py - oy) for ox, oy in net_others[net])
        total += min_d

    return total


def run(tracks, pad_positions, nets, fps=None, pads_lib=None, ref=None):
    """
    fps and pads_lib are required for this check.
    ref: if given, only evaluate that component reference.
    Returns list of hint strings.
    """
    if fps is None or pads_lib is None:
        return []

    candidates = {ref: fps[ref]} if ref and ref in fps else fps

    hints = []
    for ref, fp in candidates.items():
        current_cost = _wirelength_contribution(ref, fp, pads_lib, pad_positions, nets)
        best_rot = fp['rot']
        best_cost = current_cost

        for rot in [0, 90, 180, 270]:
            if rot == fp['rot']:
                continue
            cost = _wirelength_contribution(ref, fp, pads_lib, pad_positions, nets, rot_override=rot)
            if cost < best_cost:
                best_cost = cost
                best_rot = rot

        if best_rot != fp['rot']:
            saving = current_cost - best_cost
            hints.append(
                f"HINT  {ref}: rotate r{fp['rot']} → r{best_rot} saves {saving:.2f}mm wirelength"
            )

    return hints
