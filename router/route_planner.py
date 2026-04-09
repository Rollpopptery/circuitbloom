#!/usr/bin/env python3
"""
route_planner.py — Pre-routing analysis and planning.

Analyses the board before any traces are placed:
1. Corridor analysis — find clear horizontal/vertical channels
2. Net requirements — what each net needs (direction, distance, corridors)
3. Net conflicts — which nets compete for the same space
4. Constraint scoring — rank nets by routing difficulty
5. Layer assignment — split nets across F.Cu/B.Cu
6. Fan-out planning — determine stub direction for each IC pin
7. Route order — constrained first, flexible last

Usage:
    from route_planner import plan_routes

    plan = plan_routes(state)
    for step in plan["route_order"]:
        print(step)
"""

import math
from collections import defaultdict


def _distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _seg_intersects(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Check if two line segments intersect (approximate — bounding box check)."""
    # Quick bounding box rejection
    if max(ax1, ax2) < min(bx1, bx2) or max(bx1, bx2) < min(ax1, ax2):
        return False
    if max(ay1, ay2) < min(by1, by2) or max(by1, by2) < min(ay1, ay2):
        return False

    # Cross product test
    def cross(ox, oy, ax, ay, bx, by):
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

    d1 = cross(bx1, by1, bx2, by2, ax1, ay1)
    d2 = cross(bx1, by1, bx2, by2, ax2, ay2)
    d3 = cross(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = cross(ax1, ay1, ax2, ay2, bx2, by2)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def _ic_body_side(pin_x, pin_y, ic_center_x, ic_center_y):
    """Determine which side of the IC a pin is on."""
    dx = pin_x - ic_center_x
    dy = pin_y - ic_center_y
    if abs(dx) > abs(dy):
        return "right" if dx > 0 else "left"
    else:
        return "bottom" if dy > 0 else "top"


def _fanout_direction(pin_side):
    """Given which side of IC the pin is on, return fan-out direction as (dx, dy)."""
    return {
        "top": (0, -1),
        "bottom": (0, 1),
        "left": (-1, 0),
        "right": (1, 0),
    }[pin_side]


def analyse_components(pads):
    """Group pads by component, compute IC centers and bounding boxes."""
    components = defaultdict(list)
    for p in pads:
        if p["ref"]:
            components[p["ref"]].append(p)

    result = {}
    for ref, comp_pads in components.items():
        xs = [p["x"] for p in comp_pads]
        ys = [p["y"] for p in comp_pads]
        result[ref] = {
            "ref": ref,
            "pads": comp_pads,
            "center_x": (min(xs) + max(xs)) / 2,
            "center_y": (min(ys) + max(ys)) / 2,
            "min_x": min(xs),
            "max_x": max(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }
    return result


def analyse_nets(pads):
    """Build net info: which pads, components, positions."""
    nets = defaultdict(list)
    for p in pads:
        if p["net"]:
            nets[p["net"]].append(p)

    result = {}
    for net_name, net_pads in nets.items():
        if len(net_pads) < 2:
            continue  # single-pad nets don't need routing
        result[net_name] = {
            "net": net_name,
            "pads": net_pads,
            "pad_count": len(net_pads),
            "refs": list(set(p["ref"] for p in net_pads)),
            "positions": [(p["x"], p["y"]) for p in net_pads],
        }
    return result


def find_corridors(pads, board_width, board_height, resolution=1.0):
    """Find clear horizontal and vertical corridors.

    Scans the board at the given resolution and identifies bands
    (horizontal rows and vertical columns) that have no pads.

    Returns:
        {"horizontal": [(y, clear_x_min, clear_x_max), ...],
         "vertical": [(x, clear_y_min, clear_y_max), ...]}
    """
    # Build pad occupancy with a margin
    pad_margin = 1.0  # mm around each pad center
    pad_zones = []
    for p in pads:
        pad_zones.append((p["x"] - pad_margin, p["y"] - pad_margin,
                          p["x"] + pad_margin, p["y"] + pad_margin))

    def is_clear(x, y):
        for zx1, zy1, zx2, zy2 in pad_zones:
            if zx1 <= x <= zx2 and zy1 <= y <= zy2:
                return False
        return True

    # Scan horizontal corridors
    h_corridors = []
    y = 0
    while y < board_height:
        # Find longest clear horizontal run at this y
        x = 0
        while x < board_width:
            if is_clear(x, y):
                x_start = x
                while x < board_width and is_clear(x, y):
                    x += resolution
                length = x - x_start
                if length >= 5:  # minimum useful corridor length
                    h_corridors.append({
                        "y": round(y, 1),
                        "x_min": round(x_start, 1),
                        "x_max": round(x, 1),
                        "length": round(length, 1)
                    })
            x += resolution
        y += resolution

    # Scan vertical corridors
    v_corridors = []
    x = 0
    while x < board_width:
        y = 0
        while y < board_height:
            if is_clear(x, y):
                y_start = y
                while y < board_height and is_clear(x, y):
                    y += resolution
                length = y - y_start
                if length >= 5:
                    v_corridors.append({
                        "x": round(x, 1),
                        "y_min": round(y_start, 1),
                        "y_max": round(y, 1),
                        "length": round(length, 1)
                    })
            y += resolution
        x += resolution

    return {"horizontal": h_corridors, "vertical": v_corridors}


def plan_fanouts(pads, components):
    """Determine fan-out direction for each pin that needs routing.

    Returns:
        {(ref, pin): {"direction": (dx, dy), "side": "top/bottom/left/right"}, ...}
    """
    fanouts = {}
    for p in pads:
        if not p["net"] or not p["ref"]:
            continue
        comp = components.get(p["ref"])
        if not comp:
            continue
        side = _ic_body_side(p["x"], p["y"], comp["center_x"], comp["center_y"])
        direction = _fanout_direction(side)
        fanouts[(p["ref"], p["pin"])] = {
            "side": side,
            "direction": direction,
            "x": p["x"],
            "y": p["y"],
            "net": p["net"],
        }
    return fanouts


def find_net_conflicts(nets, components):
    """Find pairs of nets whose direct paths would cross.

    Returns list of (net_a, net_b) pairs that conflict.
    """
    conflicts = []
    net_names = list(nets.keys())

    for i in range(len(net_names)):
        for j in range(i + 1, len(net_names)):
            na = nets[net_names[i]]
            nb = nets[net_names[j]]

            # Use first two pads of each net as the "direct path"
            if len(na["positions"]) < 2 or len(nb["positions"]) < 2:
                continue

            ax1, ay1 = na["positions"][0]
            ax2, ay2 = na["positions"][1]
            bx1, by1 = nb["positions"][0]
            bx2, by2 = nb["positions"][1]

            if _seg_intersects(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
                conflicts.append((net_names[i], net_names[j]))

    return conflicts


def score_constraints(nets, components, corridors):
    """Score each net by routing difficulty (higher = more constrained).

    Factors:
    - Distance between pads (longer = harder)
    - Number of components between source and dest
    - Number of clear corridors available
    - Number of conflicting nets
    """
    scores = {}
    for net_name, net_info in nets.items():
        if len(net_info["positions"]) < 2:
            continue

        # Distance
        positions = net_info["positions"]
        min_dist = float("inf")
        max_dist = 0
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                d = _distance(positions[i][0], positions[i][1],
                              positions[j][0], positions[j][1])
                min_dist = min(min_dist, d)
                max_dist = max(max_dist, d)

        # Count pads (more pads = more complex net like GND/VCC)
        pad_count = net_info["pad_count"]

        # Longer distance and fewer pads = more constrained point-to-point signal
        # Multi-pad nets (power) are flexible — many connection options
        if pad_count <= 2:
            score = max_dist * 2  # point-to-point, distance dominates
        else:
            score = max_dist / pad_count  # power nets, flexible

        scores[net_name] = {
            "net": net_name,
            "score": round(score, 1),
            "distance": round(max_dist, 1),
            "pad_count": pad_count,
            "refs": net_info["refs"],
        }

    return scores


def suggest_layer_assignment(nets, conflicts):
    """Suggest F.Cu or B.Cu for each net to minimise conflicts.

    Simple greedy: assign conflicting nets to different layers.
    """
    conflict_graph = defaultdict(set)
    for a, b in conflicts:
        conflict_graph[a].add(b)
        conflict_graph[b].add(a)

    assignments = {}
    # Sort by number of conflicts (most constrained first)
    ordered = sorted(conflict_graph.keys(), key=lambda n: len(conflict_graph[n]), reverse=True)

    for net in ordered:
        # Check what layers neighbours use
        neighbour_layers = set()
        for nb in conflict_graph[net]:
            if nb in assignments:
                neighbour_layers.add(assignments[nb])

        if "F.Cu" not in neighbour_layers:
            assignments[net] = "F.Cu"
        elif "B.Cu" not in neighbour_layers:
            assignments[net] = "B.Cu"
        else:
            assignments[net] = "F.Cu"  # default, will need vias

    # Nets without conflicts default to F.Cu
    for net in nets:
        if net not in assignments:
            assignments[net] = "F.Cu"

    return assignments


def plan_routes(state):
    """Produce a complete routing plan from current board state.

    Args:
        state: route server state dict with "pads", "tracks", "board"

    Returns:
        Dict with analysis results and ordered routing plan.
    """
    pads = state["pads"]
    board = state.get("board", {})
    board_w = board.get("width", 55)
    board_h = board.get("height", 40)

    # Already routed nets
    routed_nets = set(t["net"] for t in state.get("tracks", []))

    # Analysis
    components = analyse_components(pads)
    nets = analyse_nets(pads)
    fanouts = plan_fanouts(pads, components)
    corridors = find_corridors(pads, board_w, board_h, resolution=1.0)
    conflicts = find_net_conflicts(nets, components)
    scores = score_constraints(nets, components, corridors)
    layers = suggest_layer_assignment(nets, conflicts)

    # Filter out already-routed nets
    unrouted = {k: v for k, v in scores.items() if k not in routed_nets}

    # Route order: highest constraint score first
    route_order = sorted(unrouted.values(), key=lambda s: s["score"], reverse=True)

    # Build per-net routing plan
    net_plans = {}
    for net_name, net_info in nets.items():
        if net_name in routed_nets:
            continue

        plan = {
            "net": net_name,
            "layer": layers.get(net_name, "F.Cu"),
            "constraint_score": scores.get(net_name, {}).get("score", 0),
            "conflicts_with": [b for a, b in conflicts if a == net_name] +
                              [a for a, b in conflicts if b == net_name],
            "pads": [],
        }

        for p in net_info["pads"]:
            key = (p["ref"], p["pin"])
            fo = fanouts.get(key, {})
            plan["pads"].append({
                "ref": p["ref"],
                "pin": p["pin"],
                "x": p["x"],
                "y": p["y"],
                "fanout_side": fo.get("side", "unknown"),
                "fanout_dir": fo.get("direction", (0, 0)),
            })

        net_plans[net_name] = plan

    return {
        "board_size": {"width": round(board_w, 1), "height": round(board_h, 1)},
        "components": {ref: {
            "center": (round(c["center_x"], 1), round(c["center_y"], 1)),
            "bbox": (round(c["min_x"], 1), round(c["min_y"], 1),
                     round(c["max_x"], 1), round(c["max_y"], 1)),
            "pad_count": len(c["pads"]),
        } for ref, c in components.items()},
        "unrouted_count": len(unrouted),
        "conflicts": conflicts,
        "route_order": route_order,
        "layer_assignments": layers,
        "net_plans": net_plans,
        "corridors": {
            "horizontal_count": len(corridors["horizontal"]),
            "vertical_count": len(corridors["vertical"]),
            "best_horizontal": sorted(corridors["horizontal"],
                                       key=lambda c: c["length"], reverse=True)[:10],
            "best_vertical": sorted(corridors["vertical"],
                                     key=lambda c: c["length"], reverse=True)[:10],
        },
    }


def print_plan(plan):
    """Pretty-print a routing plan."""
    print(f"Board: {plan['board_size']['width']}x{plan['board_size']['height']}mm")
    print(f"Unrouted nets: {plan['unrouted_count']}")
    print(f"Conflicts: {len(plan['conflicts'])}")
    print(f"Corridors: {plan['corridors']['horizontal_count']}H, {plan['corridors']['vertical_count']}V")

    print(f"\n{'=' * 60}")
    print("ROUTE ORDER (most constrained first):")
    print(f"{'=' * 60}")
    for i, s in enumerate(plan["route_order"]):
        net = s["net"]
        np_ = plan["net_plans"].get(net, {})
        layer = np_.get("layer", "?")
        conflicts = np_.get("conflicts_with", [])
        conf_str = f"  conflicts: {','.join(conflicts)}" if conflicts else ""
        print(f"  {i+1}. {net:20s} score={s['score']:5.1f}  {s['distance']:5.1f}mm  "
              f"pads={s['pad_count']}  layer={layer}{conf_str}")

        for p in np_.get("pads", []):
            print(f"       {p['ref']}.{p['pin']} ({p['x']:.1f},{p['y']:.1f}) "
                  f"fanout={p['fanout_side']} {p['fanout_dir']}")

    print(f"\nBest horizontal corridors:")
    for c in plan["corridors"]["best_horizontal"][:5]:
        print(f"  y={c['y']}  x=[{c['x_min']}-{c['x_max']}]  {c['length']}mm")

    print(f"\nBest vertical corridors:")
    for c in plan["corridors"]["best_vertical"][:5]:
        print(f"  x={c['x']}  y=[{c['y_min']}-{c['y_max']}]  {c['length']}mm")


if __name__ == "__main__":
    import json
    import urllib.request

    state = json.loads(urllib.request.urlopen("http://localhost:8084/").read())
    plan = plan_routes(state)
    print_plan(plan)
