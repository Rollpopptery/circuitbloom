"""
unnecessary_vias.py
Detects vias that are unnecessary because a through-hole pad on the same net
provides the layer transition.

A TH pad connects both layers by definition. If a single track segment
connects a via directly to a TH pad on the same net, the via is redundant —
the track can simply continue on the other layer and connect to the TH pad
directly.
"""

TOLERANCE = 0.01  # mm


def _at(x1, y1, x2, y2, tol=TOLERANCE):
    return abs(x1 - x2) < tol and abs(y1 - y2) < tol


def run(tracks, pad_positions, nets, vias=None):
    if not vias:
        return []

    # Build TH pad lookup: (x, y) -> net
    th_pads = {}
    for (ref, pnum), (ax, ay, net) in pad_positions.items():
        th_pads[(ax, ay)] = net

    # Build track endpoint lookup: (x, y, layer, net) -> other endpoint
    violations = []

    for (vx, vy, drill, annular, vnet) in vias:
        # Find all track segments that connect to this via
        connected = []
        for (x1, y1, x2, y2, w, layer, net) in tracks:
            if net != vnet:
                continue
            if _at(x1, y1, vx, vy):
                connected.append((x2, y2, layer))
            elif _at(x2, y2, vx, vy):
                connected.append((x1, y1, layer))

        # Check if any connected track leads directly to a TH pad
        for (ox, oy, layer) in connected:
            if (ox, oy) in th_pads and th_pads[(ox, oy)] == vnet:
                violations.append(
                    f"UNNECESSARY VIA: ({vx},{vy}) net={vnet} — "
                    f"track on {layer} connects directly to TH pad at ({ox},{oy}); "
                    f"via not needed for layer transition"
                )
                break  # one reason is enough per via

    return violations
