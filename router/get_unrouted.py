#!/usr/bin/env python3
"""
get_unrouted.py — Return unrouted pad pairs by cross-referencing ratsnest
against placed tracks using graph connectivity.

Usage:
    python3 get_unrouted.py
    python3 get_unrouted.py --server http://localhost:8084
"""

import json
import sys
import urllib.request
from collections import defaultdict

SERVER = "http://localhost:8084"

SNAP = 0.1  # mm — coordinate snap tolerance


def _snap(v):
    return round(round(v / SNAP) * SNAP, 4)


def _snap_pt(x, y):
    return (_snap(x), _snap(y))


def _build_connectivity(tracks, pads, vias):
    """Build union-find connectivity graph per net from placed tracks.

    Returns dict of net -> set of frozensets, where each frozenset is
    a connected component of (ref, pin) pad identifiers.
    """
    # Build pad lookup: snapped point -> (ref, pin) per net
    pad_at = defaultdict(dict)  # net -> {snapped_pt: (ref, pin)}
    for p in pads:
        net = p.get("net", "")
        if net:
            pt = _snap_pt(p["x"], p["y"])
            pad_at[net][pt] = (p["ref"], p["pin"])

    # Union-find per net
    parent = defaultdict(dict)  # net -> {node: node}

    def find(net, node):
        if parent[net].setdefault(node, node) != node:
            parent[net][node] = find(net, parent[net][node])
        return parent[net][node]

    def union(net, a, b):
        ra, rb = find(net, a), find(net, b)
        if ra != rb:
            parent[net][ra] = rb

    # Add via connections — vias connect all layers at a point
    via_pts = defaultdict(set)  # net -> set of snapped points
    for v in vias:
        net = v.get("net", "")
        if net:
            via_pts[net].add(_snap_pt(v["x"], v["y"]))

    # Union track endpoints
    for t in tracks:
        net = t.get("net", "")
        if not net:
            continue
        p1 = _snap_pt(t["x1"], t["y1"])
        p2 = _snap_pt(t["x2"], t["y2"])
        union(net, p1, p2)

    # Build connected pad pairs — map (ref, pin) to its component root
    def pad_component(net, ref, pin):
        """Find the component root for a pad."""
        pad = find(net, _snap_pt(
            next((p["x"] for p in pads if p["ref"] == ref and p["pin"] == pin), 0),
            next((p["y"] for p in pads if p["ref"] == ref and p["pin"] == pin), 0)
        ))
        return pad

    return find, pad_at, pads


def are_connected(net, ref_a, pin_a, ref_b, pin_b, find_fn, pads):
    """Check if two pads are in the same connected component."""
    pad_a = next((p for p in pads
                  if p["ref"] == ref_a and p["pin"] == pin_a and p.get("net") == net), None)
    pad_b = next((p for p in pads
                  if p["ref"] == ref_b and p["pin"] == pin_b and p.get("net") == net), None)
    if not pad_a or not pad_b:
        return False
    pt_a = _snap_pt(pad_a["x"], pad_a["y"])
    pt_b = _snap_pt(pad_b["x"], pad_b["y"])
    return find_fn(net, pt_a) == find_fn(net, pt_b)


def get_unrouted(server=SERVER):
    data     = json.loads(urllib.request.urlopen(server + "/").read())
    tracks   = data.get("tracks", [])
    pads     = data.get("pads", [])
    vias     = data.get("vias", [])
    ratsnest = json.loads(urllib.request.urlopen(server + "/ratsnest").read())

    find_fn, pad_at, _ = _build_connectivity(tracks, pads, vias)

    unrouted = {}
    for net, edges in ratsnest.items():
        missing = []
        for e in edges:
            ref_a, pin_a = e["from"].split(".", 1)
            ref_b, pin_b = e["to"].split(".", 1)
            if not are_connected(net, ref_a, pin_a, ref_b, pin_b, find_fn, pads):
                missing.append(e)
        if missing:
            unrouted[net] = missing

    return unrouted


def get_route_order(unrouted, nets_data):
    """Sort unrouted connections by routing priority.

    Primary sort: pad contention — pads shared by many unrouted routes
    are more contested and should be routed first.
    Secondary sort: obstacle count — denser routes first.
    """
    obstacle_map = {}
    for net_info in nets_data.get("nets", []):
        for pair in net_info.get("pairs", []):
            obstacle_map[(net_info["net"], pair["from"], pair["to"])] = pair["obstacles"]
            obstacle_map[(net_info["net"], pair["to"], pair["from"])] = pair["obstacles"]

    pairs = []
    for net, edges in unrouted.items():
        for e in edges:
            obs = obstacle_map.get((net, e["from"], e["to"]), 0)
            pairs.append((net, e["from"], e["to"], obs))

    pad_contention = {}
    for net, from_pad, to_pad, obs in pairs:
        pad_contention[from_pad] = pad_contention.get(from_pad, 0) + 1
        pad_contention[to_pad]   = pad_contention.get(to_pad,   0) + 1

    pairs.sort(key=lambda x: -(pad_contention[x[1]] + pad_contention[x[2]] + x[3]))

    return pairs


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=SERVER)
    args = parser.parse_args()

    unrouted = get_unrouted(args.server)
    total = sum(len(e) for e in unrouted.values())
    print(f"Unrouted connections: {total}")
    for net, edges in sorted(unrouted.items()):
        for e in edges:
            print(f"  {net:20s} {e['from']:15s} -> {e['to']}")