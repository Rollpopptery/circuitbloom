#!/usr/bin/env python3
"""
route_optimise_junctions.py — Junction finder optimisation pass.

Strategy:
    For each net with multiple chains, find pairs of chains on the same
    layer whose closest approach is within MAX_JUNCTION_MM. Introduce a
    junction at the closest point — splitting both segments and connecting
    them with a short connector segment.

    The saving criterion is simply minimising connector length — shorter
    connectors are better. Any connector under MAX_JUNCTION_MM is accepted
    if it passes clearance.

    After junctions are introduced, run route_optimise_r1 and
    route_optimise_r2 to clean up angles and shorten the result.

Usage:
    from route_optimise_junctions import optimise_pass
    stats = optimise_pass(state, grid, lock)
"""

import math
import random
from collections import defaultdict

from dpcb_router_grid import GRID_PITCH, _line_cells_fast

SNAP_TOL = 0.05
MAX_JUNCTION_MM = 1.0  # maximum connector length to consider


def _snap(x, y):
    return (round(x / SNAP_TOL) * SNAP_TOL,
            round(y / SNAP_TOL) * SNAP_TOL)


def _seg_length(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


def _closest_point_on_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return x1, y1, 0.0
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    return x1 + t * dx, y1 + t * dy, t


def _seg_to_seg_closest(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Find closest approach between two segments. Returns (pa, pb, dist)."""
    candidates = []

    cx, cy, _ = _closest_point_on_segment(ax1, ay1, bx1, by1, bx2, by2)
    candidates.append(((ax1, ay1), (cx, cy)))
    cx, cy, _ = _closest_point_on_segment(ax2, ay2, bx1, by1, bx2, by2)
    candidates.append(((ax2, ay2), (cx, cy)))
    cx, cy, _ = _closest_point_on_segment(bx1, by1, ax1, ay1, ax2, ay2)
    candidates.append(((cx, cy), (bx1, by1)))
    cx, cy, _ = _closest_point_on_segment(bx2, by2, ax1, ay1, ax2, ay2)
    candidates.append(((cx, cy), (bx2, by2)))

    dax, day = ax2 - ax1, ay2 - ay1
    dbx, dby = bx2 - bx1, by2 - by1
    dx, dy = ax1 - bx1, ay1 - by1
    a = dax * dax + day * day
    e = dbx * dbx + dby * dby
    f = dbx * dx + dby * dy

    if a > 1e-12 and e > 1e-12:
        b = dax * dbx + day * dby
        denom = a * e - b * b
        if abs(denom) > 1e-12:
            s = max(0.0, min(1.0, (b * f - e * (dax * dx + day * dy)) / denom))
            t = max(0.0, min(1.0, (b * s + f) / e))
            pa = (ax1 + s * dax, ay1 + s * day)
            pb = (bx1 + t * dbx, by1 + t * dby)
            candidates.append((pa, pb))

    best = min(candidates, key=lambda c: math.hypot(c[0][0]-c[1][0], c[0][1]-c[1][1]))
    pa, pb = best
    dist = math.hypot(pa[0]-pb[0], pa[1]-pb[1])
    return pa, pb, dist


# ============================================================
# CHAIN RECONSTRUCTION
# ============================================================

def _reconstruct_chains(tracks, pads, vias):
    fixed_pts = set()
    for p in pads:
        fixed_pts.add(_snap(p["x"], p["y"]))
    for v in vias:
        fixed_pts.add(_snap(v["x"], v["y"]))

    net_tracks = defaultdict(list)
    for t in tracks:
        if t.get("net"):
            net_tracks[t["net"]].append(t)

    net_chains = {}

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
            if len(edges) != 2 or node in fixed_pts:
                split_nodes.add(node)

        used_pairs = set()
        chains = []

        for start in split_nodes:
            for neighbor, seg in adj[start]:
                pair = (min(start, neighbor), max(start, neighbor))
                if pair in used_pairs:
                    continue

                chain_segs = [seg]
                used_pairs.add(pair)
                curr = neighbor

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
                    chain_segs.append(s)
                    curr = nb

                if chain_segs:
                    chains.append(chain_segs)

        if chains:
            net_chains[net] = chains

    return net_chains


# ============================================================
# CLEARANCE TEST
# ============================================================

def _test_segment_clearance(grid, x1, y1, x2, y2, layer_name, net, width):
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
                occ = int(occ_grid[ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - half_w)
                dy_out = max(0, abs(dy) - half_w)
                if math.hypot(dx_out, dy_out) * GRID_PITCH < design_rule:
                    return False

    return True


# ============================================================
# GRID HELPERS
# ============================================================

def _clear_segment(grid, seg, nid):
    layer_id = grid.layer_ids.get(seg["layer"])
    if layer_id is None:
        return
    w_cells = max(1, int(round(float(seg.get("width", 0.25) or 0.25) / GRID_PITCH)))
    half_w = w_cells // 2
    gx1, gy1 = grid.mm_to_grid(seg["x1"], seg["y1"])
    gx2, gy2 = grid.mm_to_grid(seg["x2"], seg["y2"])
    for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
        for dy in range(-half_w, half_w + 1):
            for dx in range(-half_w, half_w + 1):
                grid.clear_cell(layer_id, cx + dx, cy + dy, nid)


def _mark_seg(grid, x1, y1, x2, y2, layer, net, width):
    layer_id = grid.layer_ids.get(layer, 0)
    nid = grid.net_ids.get(net, 0)
    w_cells = max(1, int(round(width / GRID_PITCH)))
    grid.mark_track(x1, y1, x2, y2, w_cells, layer_id, nid)


def _seg_key(t):
    return (_snap(t["x1"], t["y1"]), _snap(t["x2"], t["y2"]))


# ============================================================
# MAIN OPTIMISATION PASS
# ============================================================

def optimise_pass(state, grid, lock):
    with lock:
        tracks = list(state["tracks"])
        pads = list(state["pads"])
        vias = list(state["vias"])

    net_chains = _reconstruct_chains(tracks, pads, vias)

    multi_chain_nets = {net: chains for net, chains in net_chains.items()
                        if len(chains) >= 2}

    if not multi_chain_nets:
        return {"joined": False, "reason": "no nets with multiple chains"}

    net = random.choice(list(multi_chain_nets.keys()))
    chains = multi_chain_nets[net]
    width = float(chains[0][0].get("width", 0.25) or 0.25)

    # Find closest pair of segments across all chain pairs — minimise connector length
    best = None
    best_dist = MAX_JUNCTION_MM

    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            for seg_a in chains[i]:
                for seg_b in chains[j]:
                    if seg_a["layer"] != seg_b["layer"]:
                        continue
                    pa, pb, dist = _seg_to_seg_closest(
                        seg_a["x1"], seg_a["y1"], seg_a["x2"], seg_a["y2"],
                        seg_b["x1"], seg_b["y1"], seg_b["x2"], seg_b["y2"]
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best = {
                            "seg_a": seg_a,
                            "seg_b": seg_b,
                            "pa": pa,
                            "pb": pb,
                            "dist": dist,
                            "layer": seg_a["layer"],
                        }

    if not best:
        return {"joined": False, "reason": "no chains close enough"}

    seg_a = best["seg_a"]
    seg_b = best["seg_b"]
    pa = best["pa"]
    pb = best["pb"]
    layer = best["layer"]
    nid = grid.net_ids.get(net, 0)

    # Clear old segments
    _clear_segment(grid, seg_a, nid)
    _clear_segment(grid, seg_b, nid)

    # Build replacement segments
    # seg_a splits into: seg_a_start->pa, pa->seg_a_end
    # seg_b splits into: seg_b_start->pb, pb->seg_b_end
    # connector: pa->pb
    seg_a_pieces = [
        {"x1": seg_a["x1"], "y1": seg_a["y1"],
         "x2": round(pa[0], 3), "y2": round(pa[1], 3),
         "layer": layer, "width": width, "net": net},
        {"x1": round(pa[0], 3), "y1": round(pa[1], 3),
         "x2": seg_a["x2"], "y2": seg_a["y2"],
         "layer": layer, "width": width, "net": net},
    ]
    seg_b_pieces = [
        {"x1": seg_b["x1"], "y1": seg_b["y1"],
         "x2": round(pb[0], 3), "y2": round(pb[1], 3),
         "layer": layer, "width": width, "net": net},
        {"x1": round(pb[0], 3), "y1": round(pb[1], 3),
         "x2": seg_b["x2"], "y2": seg_b["y2"],
         "layer": layer, "width": width, "net": net},
    ]
    connector = {"x1": round(pa[0], 3), "y1": round(pa[1], 3),
                 "x2": round(pb[0], 3), "y2": round(pb[1], 3),
                 "layer": layer, "width": width, "net": net}

    # Filter degenerate segments
    seg_a_pieces = [s for s in seg_a_pieces
                    if _seg_length(s["x1"], s["y1"], s["x2"], s["y2"]) > 1e-4]
    seg_b_pieces = [s for s in seg_b_pieces
                    if _seg_length(s["x1"], s["y1"], s["x2"], s["y2"]) > 1e-4]
    all_new = seg_a_pieces + seg_b_pieces
    if best["dist"] > 1e-4:
        all_new.append(connector)

    # Test clearance on all new segments
    for s in all_new:
        if not _test_segment_clearance(grid, s["x1"], s["y1"],
                                        s["x2"], s["y2"],
                                        s["layer"], net, width):
            # Restore
            _mark_seg(grid, seg_a["x1"], seg_a["y1"],
                      seg_a["x2"], seg_a["y2"], layer, net, width)
            _mark_seg(grid, seg_b["x1"], seg_b["y1"],
                      seg_b["x2"], seg_b["y2"], layer, net, width)
            return {"joined": False, "reason": "clearance violation"}

    # Accept — mark all new segments
    for s in all_new:
        _mark_seg(grid, s["x1"], s["y1"], s["x2"], s["y2"],
                  s["layer"], net, width)

    # Update state — replace seg_a and seg_b with their pieces
    key_a = _seg_key(seg_a)
    key_b = _seg_key(seg_b)

    with lock:
        new_tracks = []
        for t in state["tracks"]:
            k = _seg_key(t)
            if k == key_a:
                new_tracks.extend(seg_a_pieces)
            elif k == key_b:
                new_tracks.extend(seg_b_pieces)
            else:
                new_tracks.append(t)
        # Add connector
        if best["dist"] > 1e-4:
            new_tracks.append(connector)
        state["tracks"] = new_tracks
        state["version"] += 1

    return {
        "joined": True,
        "net": net,
        "connector_mm": round(best["dist"], 3),
    }