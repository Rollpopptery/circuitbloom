#!/usr/bin/env python3
"""
route_optimise_r1.py — Single-point rubber-band relaxation.

Strategy R1:
    Each call picks one moveable junction point at random from all
    routed nets, moves it step_mm toward the midpoint of its two
    neighbours, tests clearance, and accepts or restores.

    Only the two segments touching the chosen point are ever touched
    per call. The rest of the board is untouched. This makes each
    call fast, atomic, and safe.

    Call repeatedly for cooperative convergence — nets take turns
    relaxing, each pass potentially freeing space for neighbours.

Usage:
    from route_optimise_r1 import optimise_pass

    # Single step
    stats = optimise_pass(state, grid, lock, step_mm=0.5)

    # Many steps via curl loop:
    # for i in $(seq 200); do
    #   curl -s -X POST http://localhost:8084/ \
    #     -H "Content-Type: application/json" \
    #     -d '{"action":"optimise_r1","step_mm":0.5}'
    # done
"""

import math
import random
from collections import defaultdict

from dpcb_router_grid import GRID_PITCH, _line_cells_fast


SNAP_TOL = 0.05  # mm


def _snap(x, y):
    return (round(x / SNAP_TOL) * SNAP_TOL,
            round(y / SNAP_TOL) * SNAP_TOL)


def _length(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


# ============================================================
# CHAIN RECONSTRUCTION
# ============================================================

def _reconstruct_chains(tracks, pads, vias):
    """Reconstruct ordered segment chains per net.

    Returns list of candidate points:
        {
            "net":      str,
            "px", "py": current junction position,
            "prev_x", "prev_y": previous point,
            "next_x", "next_y": next point,
            "layer_before": layer of segment prev->curr,
            "layer_after":  layer of segment curr->next,
            "width_before": width of segment prev->curr,
            "width_after":  width of segment curr->next,
            "seg_before":   track dict for prev->curr segment,
            "seg_after":    track dict for curr->next segment,
        }
    """
    # Fixed points — pad endpoints and via positions
    fixed_pts = set()
    for p in pads:
        fixed_pts.add(_snap(p["x"], p["y"]))
    for v in vias:
        fixed_pts.add(_snap(v["x"], v["y"]))

    # Group tracks by net
    net_tracks = defaultdict(list)
    for t in tracks:
        if t.get("net"):
            net_tracks[t["net"]].append(t)

    candidates = []

    for net, segs in net_tracks.items():
        # Build adjacency
        adj = defaultdict(list)
        for t in segs:
            k1 = _snap(t["x1"], t["y1"])
            k2 = _snap(t["x2"], t["y2"])
            if k1 == k2:
                continue
            adj[k1].append((k2, t))
            adj[k2].append((k1, t))

        # Split nodes
        split_nodes = set()
        for node, edges in adj.items():
            if len(edges) != 2 or node in fixed_pts:
                split_nodes.add(node)

        used_pairs = set()

        for start in split_nodes:
            for neighbor, seg in adj[start]:
                pair = (min(start, neighbor), max(start, neighbor))
                if pair in used_pairs:
                    continue

                # Walk chain
                points = [start]
                segs_in_chain = []

                prev = start
                curr = neighbor
                segs_in_chain.append(seg)
                used_pairs.add(pair)
                points.append(curr)

                while curr not in split_nodes:
                    next_edge = None
                    for nb, s in adj[curr]:
                        np_pair = (min(curr, nb), max(curr, nb))
                        if np_pair not in used_pairs:
                            next_edge = (nb, s)
                            break
                    if next_edge is None:
                        break
                    nb, s = next_edge
                    np_pair = (min(curr, nb), max(curr, nb))
                    used_pairs.add(np_pair)
                    segs_in_chain.append(s)
                    points.append(nb)
                    curr = nb

                if len(points) < 3:
                    continue

                # Emit one candidate per intermediate point
                for i in range(1, len(points) - 1):
                    pt = points[i]
                    if pt in fixed_pts:
                        continue

                    prev_pt = points[i - 1]
                    next_pt = points[i + 1]
                    seg_b = segs_in_chain[i - 1]
                    seg_a = segs_in_chain[i]

                    candidates.append({
                        "net": net,
                        "px": pt[0],
                        "py": pt[1],
                        "prev_x": prev_pt[0],
                        "prev_y": prev_pt[1],
                        "next_x": next_pt[0],
                        "next_y": next_pt[1],
                        "layer_before": seg_b["layer"],
                        "layer_after": seg_a["layer"],
                        "width_before": float(seg_b.get("width", 0.25) or 0.25),
                        "width_after": float(seg_a.get("width", 0.25) or 0.25),
                        "seg_before": seg_b,
                        "seg_after": seg_a,
                    })

    return candidates


# ============================================================
# CLEARANCE TEST
# ============================================================
def _test_segment_clearance(grid, x1, y1, x2, y2, layer_name, net, width):
    """Test if a segment passes clearance. Returns True if clear.

    Uses grid.get_cell() which checks both pad_grid (permanent pad copper
    and clearance zones) and route_grid (placed traces). This ensures the
    optimiser never moves a trace into a pad clearance zone.
    """
    layer_id = grid.layer_ids.get(layer_name)
    if layer_id is None:
        return False

    nid_self = grid.net_ids.get(net, -999)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    half_w = w_cells // 2
    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    scan_r = half_w + clearance_cells
    design_rule = clearance_cells * GRID_PITCH

    gx1, gy1 = grid.mm_to_grid(x1, y1)
    gx2, gy2 = grid.mm_to_grid(x2, y2)

    occ_grid = grid.occupy[layer_id]

    for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                # get_cell() checks pad_grid first, then route_grid
                occ = int(occ_grid[ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - half_w)
                dy_out = max(0, abs(dy) - half_w)
                dist_cells = math.hypot(dx_out, dy_out)
                if dist_cells * GRID_PITCH < design_rule:
                    return False

    return True


# ============================================================
# GRID HELPERS
# ============================================================

def _clear_segment(grid, x1, y1, x2, y2, layer_name, net, width):
    layer_id = grid.layer_ids.get(layer_name)
    if layer_id is None:
        return
    nid = grid.net_ids.get(net, 0)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    half_w = w_cells // 2
    gx1, gy1 = grid.mm_to_grid(x1, y1)
    gx2, gy2 = grid.mm_to_grid(x2, y2)
    for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
        for dy in range(-half_w, half_w + 1):
            for dx in range(-half_w, half_w + 1):
                grid.clear_cell(layer_id, cx + dx, cy + dy, nid)


def _mark_segment(grid, x1, y1, x2, y2, layer_name, net, width):
    layer_id = grid.layer_ids.get(layer_name, 0)
    nid = grid.net_ids.get(net, 0)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    grid.mark_track(x1, y1, x2, y2, w_cells, layer_id, nid)


def _seg_key(t):
    """Unique key for a track segment by snapped endpoints."""
    return (_snap(t["x1"], t["y1"]), _snap(t["x2"], t["y2"]))


# ============================================================
# MAIN OPTIMISATION PASS
# ============================================================

def optimise_pass(state, grid, lock, step_mm=0.5):
    """Single random-point rubber-band relaxation step.

    Picks one moveable junction point at random, tries to move it
    step_mm toward the midpoint of its neighbours, tests clearance
    on the two affected segments, accepts or restores.

    Args:
        state:   route server state dict
        grid:    RouterGrid
        lock:    threading.Lock
        step_mm: move distance (default 0.5mm)

    Returns:
        dict with moved (bool), net, shortening_mm, reason
    """
    with lock:
        tracks = list(state["tracks"])
        pads = list(state["pads"])
        vias = list(state["vias"])

    candidates = _reconstruct_chains(tracks, pads, vias)

    if not candidates:
        return {"moved": False, "reason": "no moveable points"}

    # Shuffle and try candidates until one succeeds or all exhausted
    random.shuffle(candidates)

    for c in candidates:
        net = c["net"]
        px, py = c["px"], c["py"]
        prev_x, prev_y = c["prev_x"], c["prev_y"]
        next_x, next_y = c["next_x"], c["next_y"]

        # Midpoint target
        mid_x = (prev_x + next_x) / 2
        mid_y = (prev_y + next_y) / 2

        dx = mid_x - px
        dy = mid_y - py
        dist = math.hypot(dx, dy)

        if dist < 1e-6:
            continue  # already at midpoint

        move = min(step_mm, dist)
        new_x = px + (dx / dist) * move
        new_y = py + (dy / dist) * move

        old_len = (_length(prev_x, prev_y, px, py) +
                   _length(px, py, next_x, next_y))
        new_len = (_length(prev_x, prev_y, new_x, new_y) +
                   _length(new_x, new_y, next_x, next_y))

        if new_len >= old_len - 1e-6:
            continue  # no improvement

        seg_b = c["seg_before"]
        seg_a = c["seg_after"]

        # Clear old segments from grid
        _clear_segment(grid, seg_b["x1"], seg_b["y1"],
                       seg_b["x2"], seg_b["y2"],
                       c["layer_before"], net, c["width_before"])
        _clear_segment(grid, seg_a["x1"], seg_a["y1"],
                       seg_a["x2"], seg_a["y2"],
                       c["layer_after"], net, c["width_after"])

        # Test new segments
        ok_b = _test_segment_clearance(
            grid, prev_x, prev_y, new_x, new_y,
            c["layer_before"], net, c["width_before"])
        ok_a = _test_segment_clearance(
            grid, new_x, new_y, next_x, next_y,
            c["layer_after"], net, c["width_after"])

        if ok_b and ok_a:
            # Accept — mark new segments on grid
            _mark_segment(grid, prev_x, prev_y, new_x, new_y,
                          c["layer_before"], net, c["width_before"])
            _mark_segment(grid, new_x, new_y, next_x, next_y,
                          c["layer_after"], net, c["width_after"])

            # Replace the two segments in state atomically
            key_b = _seg_key(seg_b)
            key_a = _seg_key(seg_a)

            new_seg_b = {
                "x1": round(prev_x, 3), "y1": round(prev_y, 3),
                "x2": round(new_x, 3),  "y2": round(new_y, 3),
                "layer": c["layer_before"],
                "width": c["width_before"],
                "net": net,
            }
            new_seg_a = {
                "x1": round(new_x, 3),  "y1": round(new_y, 3),
                "x2": round(next_x, 3), "y2": round(next_y, 3),
                "layer": c["layer_after"],
                "width": c["width_after"],
                "net": net,
            }

            with lock:
                new_tracks = []
                replaced_b = False
                replaced_a = False
                for t in state["tracks"]:
                    k = _seg_key(t)
                    if k == key_b and not replaced_b:
                        new_tracks.append(new_seg_b)
                        replaced_b = True
                    elif k == key_a and not replaced_a:
                        new_tracks.append(new_seg_a)
                        replaced_a = True
                    else:
                        new_tracks.append(t)
                state["tracks"] = new_tracks
                state["version"] += 1

            return {
                "moved": True,
                "net": net,
                "shortening_mm": round(old_len - new_len, 4),
            }

        else:
            # Restore old segments to grid
            _mark_segment(grid, seg_b["x1"], seg_b["y1"],
                          seg_b["x2"], seg_b["y2"],
                          c["layer_before"], net, c["width_before"])
            _mark_segment(grid, seg_a["x1"], seg_a["y1"],
                          seg_a["x2"], seg_a["y2"],
                          c["layer_after"], net, c["width_after"])

    return {"moved": False, "reason": "no improvements found"}