#!/usr/bin/env python3
"""
route_optimise_r1.py — Single-point rubber-band relaxation with via escape.

Strategy R1:
    Each call picks one moveable junction point at random.

    For normal points: move step_mm toward midpoint of neighbours
    (Laplacian smoothing / rubber band).

    For via points in clearance violation: compute escape direction
    (away from nearest violating obstacle) and move step_mm in that
    direction. This gets the via into a legal position so subsequent
    iterations can then relax it normally.

    Only pad endpoints are fixed. All other junction points including
    vias are moveable.

Usage:
    from route_optimise_r1 import optimise_pass

    stats = optimise_pass(state, grid, lock, step_mm=0.5)
"""

import math
import random
from collections import defaultdict

from dpcb_router import GRID_PITCH, _line_cells

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
    """Reconstruct ordered segment chains and emit moveable junction points.

    Only pad endpoints are fixed. Via positions are moveable.

    Returns list of candidate dicts.
    """
    pad_pts = set()
    for p in pads:
        pad_pts.add(_snap(p["x"], p["y"]))

    via_at = {}
    for v in vias:
        via_at[_snap(v["x"], v["y"])] = v

    net_tracks = defaultdict(list)
    for t in tracks:
        if t.get("net"):
            net_tracks[t["net"]].append(t)

    candidates = []

    for net, segs in net_tracks.items():
        adj = defaultdict(list)
        for t in segs:
            k1 = _snap(t["x1"], t["y1"])
            k2 = _snap(t["x2"], t["y2"])
            if k1 == k2:
                continue
            adj[k1].append((k2, t))
            adj[k2].append((k1, t))

        split_nodes = set()
        for node, edges in adj.items():
            if len(edges) != 2 or node in pad_pts:
                split_nodes.add(node)

        used_pairs = set()

        for start in split_nodes:
            for neighbor, seg in adj[start]:
                pair = (min(start, neighbor), max(start, neighbor))
                if pair in used_pairs:
                    continue

                points = [start]
                segs_in_chain = []
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
                    used_pairs.add((min(curr, nb), max(curr, nb)))
                    segs_in_chain.append(s)
                    points.append(nb)
                    curr = nb

                if len(points) < 3:
                    continue

                for i in range(1, len(points) - 1):
                    pt = points[i]
                    if pt in pad_pts:
                        continue

                    prev_pt = points[i - 1]
                    next_pt = points[i + 1]
                    seg_b = segs_in_chain[i - 1]
                    seg_a = segs_in_chain[i]

                    via = via_at.get(pt)
                    is_via = via is not None
                    via_od = float(via.get("od", 0.6)) if via else 0.6

                    candidates.append({
                        "net": net,
                        "px": pt[0], "py": pt[1],
                        "prev_x": prev_pt[0], "prev_y": prev_pt[1],
                        "next_x": next_pt[0], "next_y": next_pt[1],
                        "layer_before": seg_b["layer"],
                        "layer_after": seg_a["layer"],
                        "width_before": float(seg_b.get("width", 0.25) or 0.25),
                        "width_after": float(seg_a.get("width", 0.25) or 0.25),
                        "seg_before": seg_b,
                        "seg_after": seg_a,
                        "is_via": is_via,
                        "via_od": via_od,
                        "via": via,
                    })

    return candidates


# ============================================================
# VIOLATION CHECK AND ESCAPE DIRECTION
# ============================================================

def _via_violation(grid, x, y, net, via_od):
    """Check if a via position is in clearance violation.

    Returns (in_violation, escape_dx, escape_dy) where escape direction
    points away from the nearest violating obstacle. If not in violation
    returns (False, 0, 0).
    """
    nid_self = grid.net_ids.get(net, -999)
    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    via_r_cells = max(1, int(round((via_od / 2) / GRID_PITCH)))
    scan_r = via_r_cells + clearance_cells
    design_rule = clearance_cells * GRID_PITCH

    gx, gy = grid.mm_to_grid(x, y)

    nearest_dist = float("inf")
    nearest_dx = 0.0
    nearest_dy = 0.0

    for layer_id in range(grid.num_layers):
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = gx + dx, gy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                occ = int(grid.occupy[layer_id][ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - via_r_cells)
                dy_out = max(0, abs(dy) - via_r_cells)
                dist_cells = math.hypot(dx_out, dy_out)
                dist_mm = dist_cells * GRID_PITCH
                if dist_mm < design_rule and dist_mm < nearest_dist:
                    nearest_dist = dist_mm
                    # Escape direction: away from the obstacle
                    nearest_dx = -dx
                    nearest_dy = -dy

    if nearest_dist < float("inf"):
        # Normalise escape direction
        d = math.hypot(nearest_dx, nearest_dy)
        if d > 0:
            nearest_dx /= d
            nearest_dy /= d
        return True, nearest_dx, nearest_dy

    return False, 0.0, 0.0


def _segment_violation(grid, x1, y1, x2, y2, layer_name, net, width):
    """Check if a segment is in clearance violation.

    Returns (in_violation, escape_dx, escape_dy) at the worst point.
    """
    layer_id = grid.layer_ids.get(layer_name)
    if layer_id is None:
        return False, 0.0, 0.0

    nid_self = grid.net_ids.get(net, -999)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    half_w = w_cells // 2
    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    scan_r = half_w + clearance_cells
    design_rule = clearance_cells * GRID_PITCH

    gx1, gy1 = grid.mm_to_grid(x1, y1)
    gx2, gy2 = grid.mm_to_grid(x2, y2)

    nearest_dist = float("inf")
    nearest_dx = 0.0
    nearest_dy = 0.0

    for cx, cy in _line_cells(gx1, gy1, gx2, gy2):
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                occ = int(grid.occupy[layer_id][ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - half_w)
                dy_out = max(0, abs(dy) - half_w)
                dist_cells = math.hypot(dx_out, dy_out)
                dist_mm = dist_cells * GRID_PITCH
                if dist_mm < design_rule and dist_mm < nearest_dist:
                    nearest_dist = dist_mm
                    nearest_dx = -dx
                    nearest_dy = -dy

    if nearest_dist < float("inf"):
        d = math.hypot(nearest_dx, nearest_dy)
        if d > 0:
            nearest_dx /= d
            nearest_dy /= d
        return True, nearest_dx, nearest_dy

    return False, 0.0, 0.0


# ============================================================
# CLEARANCE TESTS
# ============================================================

def _test_segment_clearance(grid, x1, y1, x2, y2, layer_name, net, width):
    """Test if a segment passes clearance. Returns True if clear."""
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

    for cx, cy in _line_cells(gx1, gy1, gx2, gy2):
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                occ = int(grid.occupy[layer_id][ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - half_w)
                dy_out = max(0, abs(dy) - half_w)
                dist_cells = math.hypot(dx_out, dy_out)
                if dist_cells * GRID_PITCH < design_rule:
                    return False

    return True


def _test_via_clearance(grid, x, y, net, via_od):
    """Test if a via position passes clearance on all layers."""
    nid_self = grid.net_ids.get(net, -999)
    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    via_r_cells = max(1, int(round((via_od / 2) / GRID_PITCH)))
    scan_r = via_r_cells + clearance_cells
    design_rule = clearance_cells * GRID_PITCH

    gx, gy = grid.mm_to_grid(x, y)

    for layer_id in range(grid.num_layers):
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = gx + dx, gy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                occ = int(grid.occupy[layer_id][ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - via_r_cells)
                dy_out = max(0, abs(dy) - via_r_cells)
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
    for cx, cy in _line_cells(gx1, gy1, gx2, gy2):
        for dy in range(-half_w, half_w + 1):
            for dx in range(-half_w, half_w + 1):
                grid.clear_cell(layer_id, cx + dx, cy + dy, nid)


def _mark_segment(grid, x1, y1, x2, y2, layer_name, net, width):
    layer_id = grid.layer_ids.get(layer_name, 0)
    nid = grid.net_ids.get(net, 0)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    grid.mark_track(x1, y1, x2, y2, w_cells, layer_id, nid)


def _mark_via_on_grid(grid, x, y, net):
    nid = grid.net_ids.get(net, 0)
    grid.mark_via(x, y, nid)


def _clear_via_from_grid(grid, x, y, net):
    grid.mark_via(x, y, 0)


def _seg_key(t):
    return (_snap(t["x1"], t["y1"]), _snap(t["x2"], t["y2"]))


# ============================================================
# APPLY MOVE
# ============================================================

def _apply_move(state, grid, lock, c, new_x, new_y, old_len, new_len):
    """Accept a move — update grid and state atomically."""
    net = c["net"]
    px, py = c["px"], c["py"]
    prev_x, prev_y = c["prev_x"], c["prev_y"]
    next_x, next_y = c["next_x"], c["next_y"]
    seg_b = c["seg_before"]
    seg_a = c["seg_after"]
    is_via = c["is_via"]

    _mark_segment(grid, prev_x, prev_y, new_x, new_y,
                  c["layer_before"], net, c["width_before"])
    _mark_segment(grid, new_x, new_y, next_x, next_y,
                  c["layer_after"], net, c["width_after"])
    if is_via:
        _mark_via_on_grid(grid, new_x, new_y, net)

    key_b = _seg_key(seg_b)
    key_a = _seg_key(seg_a)

    new_seg_b = {
        "x1": round(prev_x, 3), "y1": round(prev_y, 3),
        "x2": round(new_x, 3),  "y2": round(new_y, 3),
        "layer": c["layer_before"], "width": c["width_before"], "net": net,
    }
    new_seg_a = {
        "x1": round(new_x, 3),  "y1": round(new_y, 3),
        "x2": round(next_x, 3), "y2": round(next_y, 3),
        "layer": c["layer_after"], "width": c["width_after"], "net": net,
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

        if is_via:
            old_key = _snap(px, py)
            state["vias"] = [
                {**v, "x": round(new_x, 3), "y": round(new_y, 3)}
                if _snap(v["x"], v["y"]) == old_key and v.get("net") == net
                else v
                for v in state["vias"]
            ]

        state["version"] += 1

    shortening = old_len - new_len if new_len is not None else 0.0
    return {
        "moved": True,
        "net": net,
        "shortening_mm": round(shortening, 4),
        "is_via": is_via,
        "escape": new_len is None,
    }


def _restore(grid, c):
    """Restore old segments and via to grid after failed test."""
    net = c["net"]
    seg_b = c["seg_before"]
    seg_a = c["seg_after"]
    _mark_segment(grid, seg_b["x1"], seg_b["y1"],
                  seg_b["x2"], seg_b["y2"],
                  c["layer_before"], net, c["width_before"])
    _mark_segment(grid, seg_a["x1"], seg_a["y1"],
                  seg_a["x2"], seg_a["y2"],
                  c["layer_after"], net, c["width_after"])
    if c["is_via"]:
        _mark_via_on_grid(grid, c["px"], c["py"], net)


# ============================================================
# MAIN OPTIMISATION PASS
# ============================================================

def optimise_pass(state, grid, lock, step_mm=0.5):
    """Single random-point rubber-band relaxation step.

    For normal points: move toward midpoint of neighbours.
    For via points in violation: escape move away from obstacle.

    Args:
        state:   route server state dict
        grid:    RouterGrid
        lock:    threading.Lock
        step_mm: move distance (default 0.5mm)

    Returns:
        dict with moved, net, shortening_mm, is_via, escape, reason
    """
    with lock:
        tracks = list(state["tracks"])
        pads = list(state["pads"])
        vias = list(state["vias"])

    candidates = _reconstruct_chains(tracks, pads, vias)

    if not candidates:
        return {"moved": False, "reason": "no moveable points"}

    random.shuffle(candidates)

    for c in candidates:
        net = c["net"]
        px, py = c["px"], c["py"]
        prev_x, prev_y = c["prev_x"], c["prev_y"]
        next_x, next_y = c["next_x"], c["next_y"]
        is_via = c["is_via"]

        # Clear old segments and via from grid before testing
        _clear_segment(grid, c["seg_before"]["x1"], c["seg_before"]["y1"],
                       c["seg_before"]["x2"], c["seg_before"]["y2"],
                       c["layer_before"], net, c["width_before"])
        _clear_segment(grid, c["seg_after"]["x1"], c["seg_after"]["y1"],
                       c["seg_after"]["x2"], c["seg_after"]["y2"],
                       c["layer_after"], net, c["width_after"])
        if is_via:
            _clear_via_from_grid(grid, px, py, net)

        # ---- Check for existing violation (via or segments) ----
        in_violation = False
        esc_dx, esc_dy = 0.0, 0.0

        if is_via:
            in_violation, esc_dx, esc_dy = _via_violation(
                grid, px, py, net, c["via_od"])

        if not in_violation:
            # Also check if either segment is currently in violation
            viol_b, edx_b, edy_b = _segment_violation(
                grid, prev_x, prev_y, px, py,
                c["layer_before"], net, c["width_before"])
            viol_a, edx_a, edy_a = _segment_violation(
                grid, px, py, next_x, next_y,
                c["layer_after"], net, c["width_after"])
            if viol_b or viol_a:
                in_violation = True
                # Average escape directions
                edx = edx_b + edx_a
                edy = edy_b + edy_a
                d = math.hypot(edx, edy)
                if d > 0:
                    esc_dx = edx / d
                    esc_dy = edy / d

        # ---- Choose move direction ----
        if in_violation and (esc_dx != 0 or esc_dy != 0):
            # Escape move — away from violation
            new_x = px + esc_dx * step_mm
            new_y = py + esc_dy * step_mm
            escape = True
        else:
            # Relaxation move — toward midpoint
            mid_x = (prev_x + next_x) / 2
            mid_y = (prev_y + next_y) / 2
            dx = mid_x - px
            dy = mid_y - py
            dist = math.hypot(dx, dy)

            if dist < 1e-6:
                _restore(grid, c)
                continue

            move = min(step_mm, dist)
            new_x = px + (dx / dist) * move
            new_y = py + (dy / dist) * move

            old_len = (_length(prev_x, prev_y, px, py) +
                       _length(px, py, next_x, next_y))
            new_len = (_length(prev_x, prev_y, new_x, new_y) +
                       _length(new_x, new_y, next_x, next_y))

            if new_len >= old_len - 1e-6:
                _restore(grid, c)
                continue

            escape = False

        old_len = (_length(prev_x, prev_y, px, py) +
                   _length(px, py, next_x, next_y))
        new_len = (_length(prev_x, prev_y, new_x, new_y) +
                   _length(new_x, new_y, next_x, next_y))

        # Test clearance of new position
        ok_b = _test_segment_clearance(
            grid, prev_x, prev_y, new_x, new_y,
            c["layer_before"], net, c["width_before"])
        ok_a = _test_segment_clearance(
            grid, new_x, new_y, next_x, next_y,
            c["layer_after"], net, c["width_after"])
        ok_via = _test_via_clearance(
            grid, new_x, new_y, net, c["via_od"]) if is_via else True

        if ok_b and ok_a and ok_via:
            return _apply_move(state, grid, lock, c,
                               new_x, new_y, old_len, new_len)
        else:
            _restore(grid, c)

    return {"moved": False, "reason": "no improvements found"}