"""
mst.py — Minimum spanning tree for pad positions.

Shared by viewer ratsnest, ratsnest_check, and repulsion_check.
Uses Prim's algorithm (efficient for small pad counts typical in PCB nets).

Usage:
    from mst import mst_edges

    pads = [("U1.3", 10.0, 5.0), ("R1.1", 15.0, 5.0), ("C1.2", 12.0, 8.0)]
    edges = mst_edges(pads)
    # returns: [((pad_a_tuple), (pad_b_tuple)), ...]
"""

import math


def mst_edges(pads):
    """
    Build MST from list of (label, x, y) tuples.
    Returns list of ((label_a, xa, ya), (label_b, xb, yb)) edges.
    """
    n = len(pads)
    if n < 2:
        return []

    # Prim's algorithm
    in_tree = [False] * n
    min_cost = [float('inf')] * n
    min_edge = [-1] * n  # index of the nearest tree node

    # Start from node 0
    in_tree[0] = True
    for j in range(1, n):
        d = _dist(pads[0], pads[j])
        min_cost[j] = d
        min_edge[j] = 0

    edges = []
    for _ in range(n - 1):
        # Find cheapest edge from tree to non-tree
        best = -1
        best_cost = float('inf')
        for j in range(n):
            if not in_tree[j] and min_cost[j] < best_cost:
                best_cost = min_cost[j]
                best = j

        if best == -1:
            break  # disconnected (shouldn't happen with real coordinates)

        in_tree[best] = True
        edges.append((pads[min_edge[best]], pads[best]))

        # Update costs for remaining non-tree nodes
        for j in range(n):
            if not in_tree[j]:
                d = _dist(pads[best], pads[j])
                if d < min_cost[j]:
                    min_cost[j] = d
                    min_edge[j] = best

    return edges


def _dist(a, b):
    dx = a[1] - b[1]
    dy = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy)
