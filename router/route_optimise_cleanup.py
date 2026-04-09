#!/usr/bin/env python3
"""
route_optimise_cleanup.py — Crossing detection and redundant segment removal.

Two passes per call:

Pass 1 — Crossing detection:
    Find pairs of same-net segments on the same layer that physically cross.
    Split both at the crossing point, creating 4 segments from 2.
    The crossing point becomes a shared node.

Pass 2 — Redundancy removal:
    A segment is redundant if its endpoints remain connected through other
    segments on the same layer without it. Remove redundant segments,
    longest first.

Usage:
    from route_optimise_cleanup import optimise_pass
    stats = optimise_pass(state, grid, lock)
"""

import math
import random
from collections import defaultdict

from dpcb_router_grid import GRID_PITCH, _line_cells_fast

SNAP_TOL = 0.05


def _snap(x, y):
    return (round(x / SNAP_TOL) * SNAP_TOL,
            round(y / SNAP_TOL) * SNAP_TOL)


def _seg_key(seg):
    k1 = (_snap(seg["x1"], seg["y1"]), seg["layer"])
    k2 = (_snap(seg["x2"], seg["y2"]), seg["layer"])
    return (min(k1, k2), max(k1, k2))


def _seg_length(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


# ============================================================
# CROSSING DETECTION
# ============================================================

def _seg_intersection(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Find intersection point of two line segments.

    Returns (x, y) if they cross in their interiors, else None.
    Does not count endpoint touches as crossings.
    """
    dax, day = ax2 - ax1, ay2 - ay1
    dbx, dby = bx2 - bx1, by2 - by1

    denom = dax * dby - day * dbx
    if abs(denom) < 1e-12:
        return None  # parallel

    dx, dy = bx1 - ax1, by1 - ay1
    t = (dx * dby - dy * dbx) / denom
    u = (dx * day - dy * dax) / denom

    # Must be strictly interior (not at endpoints)
    eps = 0.01
    if eps < t < 1 - eps and eps < u < 1 - eps:
        x = ax1 + t * dax
        y = ay1 + t * day
        return round(x, 3), round(y, 3)

    return None


def _find_and_split_crossings(segs):
    """Find crossing segment pairs and split them at crossing points.

    Returns (new_segs, found) where found is True if any crossings were split.
    """
    result = list(segs)
    found = False

    i = 0
    while i < len(result):
        seg_a = result[i]
        j = i + 1
        while j < len(result):
            seg_b = result[j]

            if seg_a["layer"] != seg_b["layer"]:
                j += 1
                continue

            pt = _seg_intersection(
                seg_a["x1"], seg_a["y1"], seg_a["x2"], seg_a["y2"],
                seg_b["x1"], seg_b["y1"], seg_b["x2"], seg_b["y2"]
            )

            if pt:
                cx, cy = pt
                w = seg_a.get("width", 0.25)
                layer = seg_a["layer"]
                net = seg_a["net"]

                # Split seg_a into two
                a1 = {"x1": seg_a["x1"], "y1": seg_a["y1"],
                      "x2": cx, "y2": cy,
                      "layer": layer, "width": w, "net": net}
                a2 = {"x1": cx, "y1": cy,
                      "x2": seg_a["x2"], "y2": seg_a["y2"],
                      "layer": layer, "width": w, "net": net}

                # Split seg_b into two
                b1 = {"x1": seg_b["x1"], "y1": seg_b["y1"],
                      "x2": cx, "y2": cy,
                      "layer": layer, "width": w, "net": net}
                b2 = {"x1": cx, "y1": cy,
                      "x2": seg_b["x2"], "y2": seg_b["y2"],
                      "layer": layer, "width": w, "net": net}

                # Filter degenerate segments
                new_segs = [s for s in [a1, a2, b1, b2]
                            if _seg_length(s["x1"], s["y1"],
                                          s["x2"], s["y2"]) > 0.01]

                # Replace seg_a and seg_b with split pieces
                result = [s for k, s in enumerate(result)
                          if k != i and k != j]
                result.extend(new_segs)
                found = True
                break  # restart search from scratch
            else:
                j += 1

        if found:
            break  # one crossing per pass — let caller loop
        i += 1

    return result, found


# ============================================================
# REDUNDANCY REMOVAL
# ============================================================

def _build_graph(segments):
    graph = defaultdict(set)
    for seg in segments:
        layer = seg["layer"]
        k1 = (_snap(seg["x1"], seg["y1"]), layer)
        k2 = (_snap(seg["x2"], seg["y2"]), layer)
        if k1 == k2:
            continue
        graph[k1].add(k2)
        graph[k2].add(k1)
    return graph


def _connected(graph, start, end):
    if start == end:
        return True
    if start not in graph:
        return False
    visited = {start}
    queue = [start]
    while queue:
        node = queue.pop()
        for nb in graph[node]:
            if nb == end:
                return True
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    return False


def _is_redundant(seg, graph_without):
    layer = seg["layer"]
    k1 = (_snap(seg["x1"], seg["y1"]), layer)
    k2 = (_snap(seg["x2"], seg["y2"]), layer)
    return _connected(graph_without, k1, k2)


def _remove_redundant(segs):
    """Remove redundant segments longest first. Returns (kept, removed)."""
    sorted_segs = sorted(segs,
                         key=lambda s: _seg_length(s["x1"], s["y1"],
                                                    s["x2"], s["y2"]),
                         reverse=True)
    kept = list(segs)
    removed = []

    for seg in sorted_segs:
        remaining = [s for s in kept if _seg_key(s) != _seg_key(seg)]
        graph_without = _build_graph(remaining)
        if _is_redundant(seg, graph_without):
            kept = remaining
            removed.append(seg)

    return kept, removed


# ============================================================
# GRID HELPERS
# ============================================================

def _clear_net(grid, segments, net):
    nid = grid.net_ids.get(net, 0)
    for seg in segments:
        layer_id = grid.layer_ids.get(seg["layer"])
        if layer_id is None:
            continue
        w_cells = max(1, int(round(float(seg.get("width", 0.25) or 0.25) / GRID_PITCH)))
        half_w = w_cells // 2
        gx1, gy1 = grid.mm_to_grid(seg["x1"], seg["y1"])
        gx2, gy2 = grid.mm_to_grid(seg["x2"], seg["y2"])
        for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
            for dy in range(-half_w, half_w + 1):
                for dx in range(-half_w, half_w + 1):
                    grid.clear_cell(layer_id, cx + dx, cy + dy, nid)


def _mark_net(grid, segments, net):
    nid = grid.net_ids.get(net, 0)
    for seg in segments:
        layer_id = grid.layer_ids.get(seg["layer"], 0)
        w_cells = max(1, int(round(float(seg.get("width", 0.25) or 0.25) / GRID_PITCH)))
        grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                        w_cells, layer_id, nid)


# ============================================================
# MAIN OPTIMISATION PASS
# ============================================================

def optimise_pass(state, grid, lock):
    """Single net cleanup pass.

    1. Find crossing segments of the same net, split at crossing point.
    2. Remove redundant segments (endpoints still connected without them).

    Returns dict with cleaned (bool), net, split, removed, kept.
    """
    with lock:
        tracks = list(state["tracks"])

    net_tracks = defaultdict(list)
    for t in tracks:
        if t.get("net"):
            net_tracks[t["net"]].append(t)

    candidates = [(net, segs) for net, segs in net_tracks.items()
                  if len(segs) >= 2]
    if not candidates:
        return {"cleaned": False, "reason": "no nets"}

    random.shuffle(candidates)

    for net, segs in candidates:
        original = list(segs)

        # Pass 1: split crossings
        new_segs, split = _find_and_split_crossings(list(segs))

        # Pass 2: remove redundant
        kept, removed = _remove_redundant(new_segs)

        if not split and not removed:
            continue

        # Apply changes
        _clear_net(grid, original, net)
        _mark_net(grid, kept, net)

        with lock:
            new_tracks = [t for t in state["tracks"] if t.get("net") != net]
            new_tracks.extend(kept)
            state["tracks"] = new_tracks
            state["version"] += 1

        return {
            "cleaned": True,
            "net": net,
            "split": split,
            "removed": len(removed),
            "kept": len(kept),
        }

    return {"cleaned": False, "reason": "no crossings or redundant segments found"}
