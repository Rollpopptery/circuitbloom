"""
rotation_trace.py
Suggests rotating a footprint if another rotation shortens its ACTUAL CONNECTED traces.

For each footprint, tries all 4 rotations and computes the sum of Manhattan
distances from each pad to the specific OTHER pad(s) it is directly connected to
via the NET topology. This is distinct from rotation_hint.py which scores against
the NEAREST pad on the same net — this scores against ALL net-connected peers,
weighted equally.

Why different from rotation_hint.py:
- rotation_hint.py: nearest-neighbour proxy (fast, misses multi-hop topology)
- rotation_trace.py: full net-peer sum (directly models trace length to ALL partners)

Returns hints as strings (not hard violations — caller handles separately).
"""

import math
from .dpcb_parser import rotate_pad


def _connected_cost(ref, fp, pads_lib, all_pad_positions, nets, rot_override=None):
    """
    Sum of Manhattan distances from each pad on `ref` to every OTHER pad it
    shares a net with, at the given rotation.
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

    # Build lookup: (ref, pnum) -> (x, y) for all pads NOT on this footprint
    other_positions = {}
    for (other_ref, other_pnum), (ax, ay, net) in all_pad_positions.items():
        if other_ref == ref:
            continue
        other_positions[(other_ref, other_pnum)] = (ax, ay)

    # Build: pad_id -> list of peer pad_ids via net membership
    net_peers = {}  # (ref, pnum) -> [(other_ref, other_pnum), ...]
    for net_name, pad_list in nets.items():
        my_in_net = [(r, p) for r, p in pad_list if r == ref]
        others_in_net = [(r, p) for r, p in pad_list if r != ref]
        for mp in my_in_net:
            net_peers.setdefault(mp, []).extend(others_in_net)

    total = 0.0
    for pnum, (px, py) in my_pads.items():
        peers = net_peers.get((ref, pnum), [])
        for (peer_ref, peer_pnum) in peers:
            if (peer_ref, peer_pnum) in other_positions:
                ox, oy = other_positions[(peer_ref, peer_pnum)]
                total += abs(px - ox) + abs(py - oy)

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
        lib_fp = fp['lib']
        if lib_fp not in pads_lib or len(pads_lib[lib_fp]) > 3:
            continue
        current_cost = _connected_cost(ref, fp, pads_lib, pad_positions, nets)
        best_rot = fp['rot']
        best_cost = current_cost

        for rot in [0, 90, 180, 270]:
            if rot == fp['rot']:
                continue
            cost = _connected_cost(ref, fp, pads_lib, pad_positions, nets, rot_override=rot)
            if cost < best_cost:
                best_cost = cost
                best_rot = rot

        if best_rot != fp['rot']:
            saving = current_cost - best_cost
            hints.append(
                f"HINT  {ref}: rotate r{fp['rot']} → r{best_rot} shortens connected traces by {saving:.2f}mm"
            )

    return hints
