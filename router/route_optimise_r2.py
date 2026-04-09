#!/usr/bin/env python3
"""
route_optimise_r2.py — 8-direction snap optimisation.

Strategy R2:
    Each call picks one segment at random and tries to snap it toward
    the nearest canonical PCB angle (0°, 45°, 90°, 135°, and their
    opposites). This makes traces look like human-routed PCB traces —
    predominantly horizontal, vertical, or 45° diagonal.

    For each segment, one endpoint is held fixed (the one closer to a
    pad or via) and the other is moved to make the segment angle
    canonical. Both the adjusted segment and its neighbour (which shares
    the moved endpoint) are tested for clearance before accepting.

    Only pad endpoints are fixed and never moved.

    Call repeatedly — each call snaps one segment. Over many iterations
    the board converges toward clean 8-direction geometry.

Usage:
    from route_optimise_r2 import optimise_pass

    stats = optimise_pass(state, grid, lock)
"""

import math
import random
from collections import defaultdict

from dpcb_router_grid import GRID_PITCH, _line_cells_fast

SNAP_TOL = 0.05  # mm
ANGLE_TOL = 2.0  # degrees — segments within this of canonical are already snapped

# Canonical angles in radians (0, 45, 90, 135, 180, 225, 270, 315)
CANONICAL_ANGLES = [i * math.pi / 4 for i in range(8)]


def _snap(x, y):
    return (round(x / SNAP_TOL) * SNAP_TOL,
            round(y / SNAP_TOL) * SNAP_TOL)


def _seg_angle(x1, y1, x2, y2):
    """Angle of segment in radians, range [-pi, pi]."""
    return math.atan2(y2 - y1, x2 - x1)


def _nearest_canonical(angle):
    """Find the nearest canonical angle to the given angle.

    Returns the canonical angle in radians.
    """
    # Normalise to [0, 2pi)
    a = angle % (2 * math.pi)
    best = None
    best_diff = float('inf')
    for ca in CANONICAL_ANGLES:
        diff = abs(a - ca)
        diff = min(diff, 2 * math.pi - diff)  # wrap around
        if diff < best_diff:
            best_diff = diff
            best = ca
    return best, math.degrees(best_diff)


def _endpoint_on_canonical(x1, y1, x2, y2, canonical_angle):
    """Given a fixed start point (x1, y1) and a canonical angle,
    compute where the end point (x2, y2) should be moved to make
    the segment canonical while preserving its length.

    Returns (new_x2, new_y2).
    """
    length = math.hypot(x2 - x1, y2 - y1)
    new_x2 = x1 + length * math.cos(canonical_angle)
    new_y2 = y1 + length * math.sin(canonical_angle)
    return round(new_x2, 3), round(new_y2, 3)


def _already_canonical(x1, y1, x2, y2, tol_deg=ANGLE_TOL):
    """Check if a segment is already at a canonical angle."""
    if math.hypot(x2 - x1, y2 - y1) < 1e-6:
        return True
    angle = _seg_angle(x1, y1, x2, y2)
    _, diff_deg = _nearest_canonical(angle)
    return diff_deg <= tol_deg


# ============================================================
# CLEARANCE TESTS (same as r1)
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
    return (_snap(t["x1"], t["y1"]), _snap(t["x2"], t["y2"]))


# ============================================================
# CANDIDATE COLLECTION
# ============================================================

def _collect_candidates(tracks, pads,vias):
    """Collect all non-canonical segments as candidates.

    For each non-canonical segment, identify which endpoint to move
    (the one NOT touching a pad) and what the canonical target is.

    Returns list of candidate dicts.
    """
    pad_pts = set()
    for p in pads:
        pad_pts.add(_snap(p["x"], p["y"]))
    for v in vias:
        pad_pts.add(_snap(v["x"], v["y"]))

    # Build adjacency to find neighbour segments
    adj = defaultdict(list)
    for i, t in enumerate(tracks):
        k1 = _snap(t["x1"], t["y1"])
        k2 = _snap(t["x2"], t["y2"])
        adj[k1].append((k2, i))
        adj[k2].append((k1, i))

    candidates = []

    for i, t in enumerate(tracks):
        x1, y1 = t["x1"], t["y1"]
        x2, y2 = t["x2"], t["y2"]
        net = t["net"]
        layer = t["layer"]
        width = float(t.get("width", 0.25) or 0.25)

        if _already_canonical(x1, y1, x2, y2):
            continue

        angle = _seg_angle(x1, y1, x2, y2)
        canonical, diff_deg = _nearest_canonical(angle)

        k1 = _snap(x1, y1)
        k2 = _snap(x2, y2)

        # Try both endpoints as the moveable end
        # Prefer moving the end that is NOT a pad
        for fixed_end, move_end in [((x1, y1, k1), (x2, y2, k2)),
                                     ((x2, y2, k2), (x1, y1, k1))]:
            fx, fy, fk = fixed_end
            mx, my, mk = move_end

            # Skip if the moveable end is a pad
            if mk in pad_pts:
                continue

            # Compute canonical position for moveable end
            # Angle from fixed to move end
            seg_angle = math.atan2(my - fy, mx - fx)
            can_angle, _ = _nearest_canonical(seg_angle)
            new_mx, new_my = _endpoint_on_canonical(fx, fy, mx, my, can_angle)

            # Find neighbour segment that shares the moveable endpoint
            neighbour = None
            for (nb_key, nb_idx) in adj[mk]:
                if nb_idx != i:
                    neighbour = tracks[nb_idx]
                    break

            candidates.append({
                "seg_idx": i,
                "seg": t,
                "net": net,
                "layer": layer,
                "width": width,
                "fixed_x": fx, "fixed_y": fy,
                "move_x": mx, "move_y": my,
                "new_x": new_mx, "new_y": new_my,
                "neighbour": neighbour,
                "diff_deg": diff_deg,
            })
            break  # only need one orientation per segment

    return candidates


# ============================================================
# MAIN OPTIMISATION PASS
# ============================================================

def optimise_pass(state, grid, lock):
    """Single random segment 8-direction snap step.

    Picks one non-canonical segment at random and tries to snap it
    to the nearest canonical PCB angle. Tests clearance on the
    adjusted segment and its neighbour. Accepts or restores.

    Args:
        state:   route server state dict
        grid:    RouterGrid
        lock:    threading.Lock

    Returns:
        dict with snapped (bool), net, diff_deg, reason
    """
    with lock:
        tracks = list(state["tracks"])
        pads = list(state["pads"])
        vias = list(state["vias"])

    candidates = _collect_candidates(tracks, pads, vias)

    if not candidates:
        return {"snapped": False, "reason": "all segments already canonical"}

    random.shuffle(candidates)

    for c in candidates:
        net = c["net"]
        seg = c["seg"]
        layer = c["layer"]
        width = c["width"]
        fx, fy = c["fixed_x"], c["fixed_y"]
        mx, my = c["move_x"], c["move_y"]
        new_mx, new_my = c["new_x"], c["new_y"]
        neighbour = c["neighbour"]

        # Skip if the new position is essentially the same
        if math.hypot(new_mx - mx, new_my - my) < 1e-4:
            continue

        # Clear old segment from grid
        _clear_segment(grid, seg["x1"], seg["y1"],
                       seg["x2"], seg["y2"],
                       layer, net, width)

        # Clear neighbour from grid if it exists
        if neighbour:
            _clear_segment(grid, neighbour["x1"], neighbour["y1"],
                           neighbour["x2"], neighbour["y2"],
                           neighbour["layer"], neighbour["net"],
                           float(neighbour.get("width", 0.25) or 0.25))

        # Test new segment (fixed -> new_move)
        ok_seg = _test_segment_clearance(
            grid, fx, fy, new_mx, new_my, layer, net, width)

        # Test adjusted neighbour if exists
        ok_neighbour = True
        if neighbour and ok_seg:
            # Find which end of the neighbour is at the moved point
            nk1 = _snap(neighbour["x1"], neighbour["y1"])
            nk2 = _snap(neighbour["x2"], neighbour["y2"])
            mk = _snap(mx, my)
            if nk1 == mk:
                n_x1, n_y1 = new_mx, new_my
                n_x2, n_y2 = neighbour["x2"], neighbour["y2"]
            else:
                n_x1, n_y1 = neighbour["x1"], neighbour["y1"]
                n_x2, n_y2 = new_mx, new_my

            ok_neighbour = _test_segment_clearance(
                grid, n_x1, n_y1, n_x2, n_y2,
                neighbour["layer"], neighbour["net"],
                float(neighbour.get("width", 0.25) or 0.25))

        if ok_seg and ok_neighbour:
            # Accept — mark new segments on grid
            _mark_segment(grid, fx, fy, new_mx, new_my, layer, net, width)

            if neighbour:
                nk1 = _snap(neighbour["x1"], neighbour["y1"])
                mk = _snap(mx, my)
                if nk1 == mk:
                    _mark_segment(grid, new_mx, new_my,
                                  neighbour["x2"], neighbour["y2"],
                                  neighbour["layer"], neighbour["net"],
                                  float(neighbour.get("width", 0.25) or 0.25))
                else:
                    _mark_segment(grid, neighbour["x1"], neighbour["y1"],
                                  new_mx, new_my,
                                  neighbour["layer"], neighbour["net"],
                                  float(neighbour.get("width", 0.25) or 0.25))

            # Update state atomically
            seg_key = _seg_key(seg)

            # Build updated segment (preserve direction)
            k1 = _snap(seg["x1"], seg["y1"])
            fk = _snap(fx, fy)
            if k1 == fk:
                new_seg = {**seg, "x2": round(new_mx, 3), "y2": round(new_my, 3)}
            else:
                new_seg = {**seg, "x1": round(new_mx, 3), "y1": round(new_my, 3)}

            with lock:
                new_tracks = []
                replaced_seg = False
                replaced_nb = False
                nb_key = _seg_key(neighbour) if neighbour else None

                for t in state["tracks"]:
                    k = _seg_key(t)
                    if k == seg_key and not replaced_seg:
                        new_tracks.append(new_seg)
                        replaced_seg = True
                    elif neighbour and k == nb_key and not replaced_nb:
                        nk1 = _snap(neighbour["x1"], neighbour["y1"])
                        mk = _snap(mx, my)
                        if nk1 == mk:
                            new_tracks.append({
                                **neighbour,
                                "x1": round(new_mx, 3),
                                "y1": round(new_my, 3),
                            })
                        else:
                            new_tracks.append({
                                **neighbour,
                                "x2": round(new_mx, 3),
                                "y2": round(new_my, 3),
                            })
                        replaced_nb = True
                    else:
                        new_tracks.append(t)

                state["tracks"] = new_tracks
                state["version"] += 1

            return {
                "snapped": True,
                "net": net,
                "diff_deg": round(c["diff_deg"], 1),
            }

        else:
            # Restore
            _mark_segment(grid, seg["x1"], seg["y1"],
                          seg["x2"], seg["y2"],
                          layer, net, width)
            if neighbour:
                _mark_segment(grid, neighbour["x1"], neighbour["y1"],
                              neighbour["x2"], neighbour["y2"],
                              neighbour["layer"], neighbour["net"],
                              float(neighbour.get("width", 0.25) or 0.25))

    return {"snapped": False, "reason": "no segments could be snapped"}