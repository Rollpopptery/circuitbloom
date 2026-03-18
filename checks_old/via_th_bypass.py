"""
via_th_bypass.py
Detects vias unnecessary because TH pads on the same net are reachable
on both layers within a limited hop depth.

A TH pad connects all copper layers. If the net on each side of a via
can reach a TH pad within MAX_HOPS track segments, the via is redundant —
the TH pad already provides the layer transition.
"""

TOLERANCE = 0.01
MAX_HOPS = 3


def _reachable_endpoints(start_x, start_y, layer, net, tracks, max_hops):
    """
    Return set of (x, y) endpoints reachable from (start_x, start_y)
    on the given layer/net within max_hops segments.
    """
    visited_points = {(start_x, start_y)}
    frontier = {(start_x, start_y)}
    reachable = set()

    for _ in range(max_hops):
        next_frontier = set()
        for (px, py) in frontier:
            for (x1, y1, x2, y2, w, lyr, n) in tracks:
                if lyr != layer or n != net:
                    continue
                if abs(x1 - px) < TOLERANCE and abs(y1 - py) < TOLERANCE:
                    other = (x2, y2)
                elif abs(x2 - px) < TOLERANCE and abs(y2 - py) < TOLERANCE:
                    other = (x1, y1)
                else:
                    continue
                if other not in visited_points:
                    visited_points.add(other)
                    next_frontier.add(other)
                    reachable.add(other)
        frontier = next_frontier
        if not frontier:
            break

    return reachable


def run(tracks, pad_positions, nets, vias=None):
    if not vias:
        return []

    # TH pad positions: (x, y) -> net
    th_pads = {(ax, ay): net for (ref, pnum), (ax, ay, net) in pad_positions.items()}

    violations = []
    for (vx, vy, drill, annular, vnet) in vias:
        # Reachable endpoints on each layer from the via
        f_reach = _reachable_endpoints(vx, vy, 'F.Cu', vnet, tracks, MAX_HOPS)
        b_reach = _reachable_endpoints(vx, vy, 'B.Cu', vnet, tracks, MAX_HOPS)

        # Check if a TH pad is reachable on each side
        f_th = [p for p in f_reach if th_pads.get(p) == vnet]
        b_th = [p for p in b_reach if th_pads.get(p) == vnet]

        if f_th and b_th:
            violations.append(
                f"UNNECESSARY VIA (TH bypass): ({vx},{vy}) net={vnet} — "
                f"TH pad reachable on F.Cu {f_th[0]} and B.Cu {b_th[0]} "
                f"within {MAX_HOPS} hops"
            )
        elif f_th and not b_th:
            # Check if via itself sits at a TH pad on B.Cu side
            if th_pads.get((vx, vy)) == vnet:
                violations.append(
                    f"UNNECESSARY VIA (TH bypass): ({vx},{vy}) net={vnet} — "
                    f"via sits on TH pad; F.Cu also reaches TH pad {f_th[0]}"
                )

    return violations
