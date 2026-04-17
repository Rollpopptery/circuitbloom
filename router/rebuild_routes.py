#!/usr/bin/env python3
"""
rebuild_routes.py — Reconstruct logical routes from raw track segments.

Given a flat list of track segments, pads, and vias, reconstructs the
pad-to-pad (and pad-to-trace) routes for each net by building a graph
and walking it.

Segment endpoints that fall within PAD_SNAP_TOL of a pad centre are
snapped exactly to that pad centre before graph construction. This
prevents tiny stub artefacts caused by floating-point imprecision in
KiCad exports where a segment endpoint is 0.01-0.05mm away from the
pad it is electrically connected to.

Usage:
    from rebuild_routes import rebuild_routes

    routes = rebuild_routes(state["tracks"], state["pads"], state["vias"])

    # Or register them into a RouteSet:
    from rebuild_routes import register_rebuilt_routes
    register_rebuilt_routes(routeset, grid, state)
"""

from collections import defaultdict
import math


SNAP_TOL = 0.1      # mm — endpoints closer than this are the same node
PAD_SNAP_TOL = 0.1   # mm — segment endpoints within this of a pad centre
                      #       are snapped exactly to the pad centre


def _snap_key(x, y):
    """Round coordinates to snap tolerance grid."""
    return (round(x / SNAP_TOL) * SNAP_TOL, round(y / SNAP_TOL) * SNAP_TOL)


def _seg_length(seg):
    dx = seg["x2"] - seg["x1"]
    dy = seg["y2"] - seg["y1"]
    return math.sqrt(dx * dx + dy * dy)


def _build_pad_snap_map(pads, net_name):
    """Return list of (x, y) pad centres for pads on net_name."""
    result = []
    for p in pads:
        if p.get("net") == net_name:
            result.append((p["x"], p["y"]))
    return result


def _snap_to_pad(x, y, pad_centres):
    """Snap (x, y) to the nearest pad centre if within PAD_SNAP_TOL."""
    best_d = PAD_SNAP_TOL
    best_x, best_y = x, y
    for px, py in pad_centres:
        d = math.hypot(x - px, y - py)
        if d < best_d:
            best_d = d
            best_x, best_y = px, py
    return best_x, best_y


def rebuild_routes(tracks, pads, vias, layer_ids=None):
    """Reconstruct logical routes from raw track segments.

    Args:
        tracks: list of {"x1","y1","x2","y2","layer","net","width"}
        pads: list of {"ref","pin","net","x","y","smd"}
        vias: list of {"x","y","net",...}
        layer_ids: optional dict {"F.Cu": 0, "B.Cu": 1, ...}

    Returns:
        List of route dicts:
            net, type, from_pad, to_pad, from_pt, to_pt,
            segments, vias, length_mm, layers
    """
    if layer_ids is None:
        layer_ids = {"F.Cu": 0, "B.Cu": 1}

    # Group tracks by net
    net_tracks = defaultdict(list)
    for t in tracks:
        if t["net"]:
            net_tracks[t["net"]].append(t)

    # Build pad lookup: snap_key -> list of pad dicts
    pad_at = defaultdict(list)
    for p in pads:
        if p["net"]:
            key = _snap_key(p["x"], p["y"])
            pad_at[key].append(p)

    # Build via lookup: snap_key -> list of vias
    via_at = defaultdict(list)
    for v in vias:
        if v.get("net"):
            key = _snap_key(v["x"], v["y"])
            via_at[key].append(v)

    all_routes = []

    for net_name, segs in net_tracks.items():
        pad_centres = _build_pad_snap_map(pads, net_name)
        routes = _rebuild_net(net_name, segs, pad_at, via_at,
                              layer_ids, pad_centres)
        all_routes.extend(routes)

    return all_routes
def _rebuild_net(net_name, segs, pad_at, via_at, layer_ids, pad_centres):
    """Rebuild routes for a single net.

    Segment endpoints are snapped to nearby pad centres before graph
    construction to eliminate tiny stub artefacts. Consecutive segment
    endpoints that are within CHAIN_TOL of each other are forced to match
    exactly to eliminate sub-grid gaps.
    """
    CHAIN_TOL = 0.15  # mm — slightly larger than SNAP_TOL to catch gaps

    seg_list = []
    for seg in segs:
        # Snap both endpoints to nearby pad centres
        x1, y1 = _snap_to_pad(seg["x1"], seg["y1"], pad_centres)
        x2, y2 = _snap_to_pad(seg["x2"], seg["y2"], pad_centres)
        seg_list.append({**seg, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    # Force matching endpoints between adjacent segments
    result = [dict(s) for s in seg_list]
    for i in range(len(result)):
        for j in range(len(result)):
            if i == j:
                continue
            # end of i close to start of j
            d = math.hypot(result[i]["x2"] - result[j]["x1"],
                           result[i]["y2"] - result[j]["y1"])
            if 0 < d < CHAIN_TOL:
                result[j]["x1"] = result[i]["x2"]
                result[j]["y1"] = result[i]["y2"]
            # end of i close to end of j
            d = math.hypot(result[i]["x2"] - result[j]["x2"],
                           result[i]["y2"] - result[j]["y2"])
            if 0 < d < CHAIN_TOL:
                result[j]["x2"] = result[i]["x2"]
                result[j]["y2"] = result[i]["y2"]
    seg_list = result

    # Layer-agnostic adjacency graph
    adj = defaultdict(set)
    for i, seg in enumerate(seg_list):
        k1 = _snap_key(seg["x1"], seg["y1"])
        k2 = _snap_key(seg["x2"], seg["y2"])
        if k1 == k2:
            continue  # degenerate segment after snapping — skip
        adj[k1].add((k2, i))
        adj[k2].add((k1, i))

    # Pad nodes
    pad_nodes = {}
    for node_key in adj:
        for p in pad_at.get(node_key, []):
            if p["net"] == net_name:
                pad_nodes[node_key] = {"ref": p["ref"], "pin": p["pin"]}
                break

    # Split nodes: degree != 2, or pad endpoint
    split_nodes = set()
    for node_key, edges in adj.items():
        if len(edges) != 2 or node_key in pad_nodes:
            split_nodes.add(node_key)

    # Walk chains between split nodes
    used_segs = set()
    routes = []

    for start_node in split_nodes:
        for neighbor, seg_idx in adj[start_node]:
            if seg_idx in used_segs:
                continue

            chain_segs = []
            chain_layers = set()
            chain_length = 0.0
            curr = neighbor
            chain_segs.append(seg_idx)
            used_segs.add(seg_idx)
            chain_layers.add(seg_list[seg_idx]["layer"])
            chain_length += _seg_length(seg_list[seg_idx])

            while curr not in split_nodes:
                next_edge = None
                for nb, si in adj[curr]:
                    if si not in used_segs:
                        next_edge = (nb, si)
                        break
                if next_edge is None:
                    break
                nb, si = next_edge
                chain_segs.append(si)
                used_segs.add(si)
                chain_layers.add(seg_list[si]["layer"])
                chain_length += _seg_length(seg_list[si])
                curr = nb

            end_node = curr

            from_pad = pad_nodes.get(start_node)
            to_pad = pad_nodes.get(end_node)

            if from_pad and to_pad:
                if from_pad["ref"] == to_pad["ref"] and \
                   from_pad["pin"] == to_pad["pin"]:
                    continue
                route_type = "pin_to_pin"
            elif from_pad or to_pad:
                route_type = "pin_to_trace"
            else:
                continue  # trace-to-trace junction artefact

            # Collect vias along the chain
            chain_via_keys = set()
            for si in chain_segs:
                seg = seg_list[si]
                for key in [_snap_key(seg["x1"], seg["y1"]),
                            _snap_key(seg["x2"], seg["y2"])]:
                    for v in via_at.get(key, []):
                        if v.get("net") == net_name:
                            chain_via_keys.add(key)

            routes.append({
                "net": net_name,
                "type": route_type,
                "from_pad": from_pad,
                "to_pad": to_pad,
                "from_pt": start_node,
                "to_pt": end_node,
                "segments": [seg_list[si] for si in chain_segs],
                "vias": [via_at[k][0] for k in chain_via_keys],
                "length_mm": round(chain_length, 3),
                "layers": sorted(chain_layers),
            })

    return routes


def register_rebuilt_routes(routeset, grid, state):
    """Rebuild routes from state and register them into a RouteSet."""
    from dpcb_router import GRID_PITCH

    tracks = state.get("tracks", [])
    pads = state.get("pads", [])
    vias = state.get("vias", [])

    layer_ids = {name: idx for idx, name in grid.layer_names.items()}
    routes = rebuild_routes(tracks, pads, vias, layer_ids)

    count = 0
    for r in routes:
        net_id = grid.net_ids.get(r["net"])
        if not net_id:
            continue

        fx, fy = r["from_pt"]
        tx, ty = r["to_pt"]
        gx1 = int(round(fx / GRID_PITCH))
        gy1 = int(round(fy / GRID_PITCH))
        gx2 = int(round(tx / GRID_PITCH))
        gy2 = int(round(ty / GRID_PITCH))

        first_seg = r["segments"][0]
        last_seg = r["segments"][-1]
        layer1 = layer_ids.get(first_seg["layer"], 0)
        layer2 = layer_ids.get(last_seg["layer"], 0)

        src_pad = (gx1, gy1, layer1)
        dst_pad = (gx2, gy2, layer2)

        routeset.register_route(net_id, src_pad, dst_pad)
        count += 1

    return count


def print_routes(routes):
    """Pretty-print reconstructed routes."""
    by_net = defaultdict(list)
    for r in routes:
        by_net[r["net"]].append(r)

    for net_name in sorted(by_net):
        net_routes = by_net[net_name]
        print(f"\n{net_name}: {len(net_routes)} route(s)")
        for r in net_routes:
            src = (f"{r['from_pad']['ref']}.{r['from_pad']['pin']}"
                   if r["from_pad"]
                   else f"({r['from_pt'][0]:.1f},{r['from_pt'][1]:.1f})")
            dst = (f"{r['to_pad']['ref']}.{r['to_pad']['pin']}"
                   if r["to_pad"]
                   else f"({r['to_pt'][0]:.1f},{r['to_pt'][1]:.1f})")
            via_str = f", {len(r['vias'])} via(s)" if r["vias"] else ""
            print(f"  {r['type']:16s}  {src:12s} -> {dst:12s}"
                  f"  {r['length_mm']:6.1f}mm  {','.join(r['layers'])}{via_str}")


def diagnose_routes(routes):
    """Print diagnostic summary — use after rebuild to verify quality."""
    total = len(routes)
    stub_threshold = 0.5  # mm

    stub_violations = []
    multi_layer = 0
    single_layer = 0

    for r in routes:
        segs = r["segments"]
        if not segs:
            continue

        first_len = _seg_length(segs[0])
        last_len = _seg_length(segs[-1])

        if first_len < stub_threshold or last_len < stub_threshold:
            stub_violations.append({
                "net": r["net"],
                "from": r["from_pad"],
                "to": r["to_pad"],
                "first_len": round(first_len, 4),
                "last_len": round(last_len, 4),
                "first_layer": segs[0]["layer"],
                "last_layer": segs[-1]["layer"],
            })

        if len(r["layers"]) > 1:
            multi_layer += 1
        else:
            single_layer += 1

    print(f"\n{'=' * 60}")
    print(f"REBUILD DIAGNOSTICS")
    print(f"{'=' * 60}")
    print(f"  Total routes       : {total}")
    print(f"  Single-layer       : {single_layer}")
    print(f"  Multi-layer        : {multi_layer}")
    print(f"  Stub violations    : {len(stub_violations)}  "
          f"(first or last seg < {stub_threshold}mm)")

    if stub_violations:
        print(f"\n  First 10 stub violations:")
        for v in stub_violations[:10]:
            src = (f"{v['from']['ref']}.{v['from']['pin']}"
                   if v["from"] else "?")
            dst = (f"{v['to']['ref']}.{v['to']['pin']}"
                   if v["to"] else "?")
            print(f"    {v['net']:20s}  {src:12s} -> {dst:12s}"
                  f"  first={v['first_len']}mm ({v['first_layer']})"
                  f"  last={v['last_len']}mm ({v['last_layer']})")
    else:
        print(f"\n  No stub violations — reconstruction is clean.")

    print(f"{'=' * 60}\n")

    return stub_violations


def _snap_segments_to_chain(seg_list):
    """Snap consecutive segment endpoints to match exactly.
    
    Sorts segments into chains by proximity and forces shared endpoints
    to be identical, eliminating sub-grid gaps between adjacent segments.
    """
    if len(seg_list) < 2:
        return seg_list

    # Build adjacency by proximity
    CHAIN_TOL = 0.15  # mm — slightly larger than SNAP_TOL to catch gaps

    result = [dict(s) for s in seg_list]

    for i in range(len(result)):
        for j in range(len(result)):
            if i == j:
                continue
            # Check if end of i is close to start of j
            d = math.hypot(result[i]["x2"] - result[j]["x1"],
                           result[i]["y2"] - result[j]["y1"])
            if d < CHAIN_TOL and d > 0:
                result[j]["x1"] = result[i]["x2"]
                result[j]["y1"] = result[i]["y2"]
            # Check if end of i is close to end of j
            d = math.hypot(result[i]["x2"] - result[j]["x2"],
                           result[i]["y2"] - result[j]["y2"])
            if d < CHAIN_TOL and d > 0:
                result[j]["x2"] = result[i]["x2"]
                result[j]["y2"] = result[i]["y2"]

    return result

if __name__ == "__main__":
    import json
    import urllib.request

    url = "http://localhost:8084/"
    data = json.loads(urllib.request.urlopen(url).read())

    tracks = data.get("tracks", [])
    pads = data.get("pads", [])
    vias = data.get("vias", [])

    print(f"Loaded: {len(tracks)} tracks, {len(pads)} pads, {len(vias)} vias")

    routes = rebuild_routes(tracks, pads, vias)
    print(f"Reconstructed: {len(routes)} routes")

    diagnose_routes(routes)
    print_routes(routes)