#!/usr/bin/env python3
"""
trace_transform.py — Retrieve and transform traces from the pattern database.

Given a query (source pad, destination pad, net), retrieves matching traces
from the database and transforms them to fit the new board's geometry.

Transform: translate to origin, rotate to match angle, scale to match length,
translate to new source position.

Usage:
    from trace_transform import retrieve_and_transform

    candidates = retrieve_and_transform(
        collection, pads, tracks, "SPI_SCK",
        source=(22.175, 29.65), dest=(44.5, 13.0),
        n=10
    )

    for trace in candidates:
        print(trace["segments"])  # transformed segments ready to place
"""

import json
import math


def _compute_transform(stored_from, stored_to, query_from, query_to):
    """Compute rotation, scale, and translation to map stored trace to query.

    Args:
        stored_from: (x, y) stored trace source
        stored_to: (x, y) stored trace destination
        query_from: (x, y) query source
        query_to: (x, y) query destination

    Returns:
        dict with rotation (radians), scale, translate (x, y)
    """
    stored_angle = math.atan2(
        stored_to[1] - stored_from[1],
        stored_to[0] - stored_from[0]
    )
    query_angle = math.atan2(
        query_to[1] - query_from[1],
        query_to[0] - query_from[0]
    )
    rotation = query_angle - stored_angle

    stored_len = math.hypot(
        stored_to[0] - stored_from[0],
        stored_to[1] - stored_from[1]
    )
    query_len = math.hypot(
        query_to[0] - query_from[0],
        query_to[1] - query_from[1]
    )

    scale = query_len / stored_len if stored_len > 0 else 1.0

    return {
        "rotation": rotation,
        "scale": scale,
        "stored_from": stored_from,
        "query_from": query_from,
        "stored_angle": stored_angle,
        "query_angle": query_angle,
        "stored_len": stored_len,
        "query_len": query_len,
    }


def _transform_point(x, y, stored_from, rotation, scale, query_from):
    """Transform a single point from stored coordinates to query coordinates.

    Args:
        x, y: point in stored board coordinates
        stored_from: (x, y) stored trace source
        rotation: angle in radians
        scale: length ratio
        query_from: (x, y) query source

    Returns:
        (new_x, new_y)
    """
    dx = x - stored_from[0]
    dy = y - stored_from[1]

    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    rx = dx * cos_r - dy * sin_r
    ry = dx * sin_r + dy * cos_r

    rx *= scale
    ry *= scale

    new_x = rx + query_from[0]
    new_y = ry + query_from[1]

    return round(new_x, 3), round(new_y, 3)


def transform_trace(meta, query_from, query_to, target_layers=None):
    """Transform a stored trace to fit a new source/destination.

    Args:
        meta: metadata dict from ChromaDB (contains segments, vias,
              from_x, from_y, to_x, to_y)
        query_from: (x, y) new source position
        query_to: (x, y) new destination position
        target_layers: dict of layer_index -> layer_name for the target board
                       If None, defaults to {0: "F.Cu", 1: "B.Cu"}

    Returns:
        dict with:
            segments: list of [x1, y1, x2, y2, layer]
            vias: list of [x, y]
            transform: dict with rotation, scale, etc.
            source_meta: original metadata
    """
    GRID = 0.1   # mm — snap all coordinates to this grid
    STITCH_TOL = 0.15  # mm — stitch consecutive endpoints within this distance

    def snap(v):
        return round(round(v / GRID) * GRID, 4)

    stored_from = (meta["from_x"], meta["from_y"])
    stored_to   = (meta["to_x"],   meta["to_y"])

    xform    = _compute_transform(stored_from, stored_to, query_from, query_to)
    rotation = xform["rotation"]
    scale    = xform["scale"]

    if target_layers is None:
        target_layers = {0: "F.Cu", 1: "B.Cu"}

    stored_segments = json.loads(meta.get("segments", "[]"))
    new_segments = []
    for seg in stored_segments:
        x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
        layer_idx = seg[4] if len(seg) > 4 else 0

        if isinstance(layer_idx, int):
            layer = target_layers.get(layer_idx, target_layers.get(0, "F.Cu"))
        else:
            layer = layer_idx

        nx1, ny1 = _transform_point(x1, y1, stored_from, rotation, scale, query_from)
        nx2, ny2 = _transform_point(x2, y2, stored_from, rotation, scale, query_from)

        new_segments.append([snap(nx1), snap(ny1), snap(nx2), snap(ny2), layer])

    # Stitch consecutive segment endpoints to match exactly
    for i in range(len(new_segments) - 1):
        x2, y2 = new_segments[i][2], new_segments[i][3]
        x1, y1 = new_segments[i + 1][0], new_segments[i + 1][1]
        if 0 < math.hypot(x2 - x1, y2 - y1) < STITCH_TOL:
            new_segments[i + 1][0] = x2
            new_segments[i + 1][1] = y2

    # Remove zero-length segments
    new_segments = [s for s in new_segments
                    if math.hypot(s[2] - s[0], s[3] - s[1]) > 1e-6]

    stored_vias = json.loads(meta.get("vias", "[]"))
    new_vias = []
    for via in stored_vias:
        vx, vy = via[0], via[1]
        nvx, nvy = _transform_point(vx, vy, stored_from, rotation, scale, query_from)
        new_vias.append([snap(nvx), snap(nvy)])

    return {
        "segments":        new_segments,
        "vias":            new_vias,
        "transform":       xform,
        "scale_distortion": round(abs(xform["scale"] - 1.0), 3),
        "source_meta":     meta,
    }


def retrieve_and_transform(collection, pads, tracks, route_net, source, dest,
                           n=10, where=None, target_layers=None):
    """Retrieve matching traces and transform them to fit the query geometry.

    Args:
        collection: ChromaDB collection
        pads: list of pad dicts from board state
        tracks: list of track dicts already placed on board — passed to
                search_patterns so the query image reflects current routing state
        route_net: net being routed
        source: (x, y) source pad position
        dest: (x, y) destination pad position
        n: number of results
        where: optional ChromaDB filter
        target_layers: dict of layer_index -> layer_name for the target board
                       If None, defaults to {0: "F.Cu", 1: "B.Cu"}

    Returns:
        list of transformed trace dicts, sorted by scale distortion
    """
    from trace_patterndb import search_patterns

    results = search_patterns(collection, pads, tracks, route_net, source, dest,
                              n=n, where=where)

    candidates = []
    for meta, dist in results:
        transformed = transform_trace(meta, source, dest, target_layers)
        transformed["match_distance"] = dist
        candidates.append(transformed)

    candidates.sort(key=lambda c: c["scale_distortion"])

    return candidates


def print_candidates(candidates):
    """Pretty-print transformed candidates."""
    for i, c in enumerate(candidates):
        meta = c["source_meta"]
        xform = c["transform"]
        rot_deg = math.degrees(xform["rotation"])

        print(f"\n  [{i+1}] match={c['match_distance']:.4f}  "
              f"scale={xform['scale']:.3f}  distortion={c['scale_distortion']:.3f}")
        print(f"      [{meta.get('board', '?')}] {meta.get('net', '?')} "
              f"{meta.get('from_ref', '?')}.{meta.get('from_pin', '?')} -> "
              f"{meta.get('to_ref', '?')}.{meta.get('to_pin', '?')}")
        print(f"      rot={rot_deg:.1f}°  "
              f"segs={len(c['segments'])}  vias={len(c['vias'])}")


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    import argparse
    import os
    import sys
    import urllib.request

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

    from pad_pattern_render import count_component_pins, choose_source_dest
    from trace_patterndb import _load_dino

    import chromadb

    SERVER = "http://localhost:8084"

    parser = argparse.ArgumentParser(description="Retrieve and transform traces")
    parser.add_argument("--from-pad", required=True, help="Source pad (e.g. XU1.18)")
    parser.add_argument("--to-pad", required=True, help="Destination pad (e.g. U4.12)")
    parser.add_argument("--net", required=True, help="Net name")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "trace_pattern_collection"))
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()

    print("Loading DINOv2...")
    _load_dino()

    client = chromadb.PersistentClient(path=os.path.abspath(args.db))
    collection = client.get_collection("trace_patterns")
    print(f"Collection: {collection.count()} patterns")

    print("Fetching board state...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    pads = data.get("pads", [])
    tracks = data.get("tracks", [])
    pad_counts = count_component_pins(pads)

    def find_pad(ref_pin):
        ref, pin = ref_pin.split(".", 1)
        for p in pads:
            if p["ref"] == ref and p["pin"] == pin:
                return p
        return None

    src_pad = find_pad(args.from_pad)
    dst_pad = find_pad(args.to_pad)
    if not src_pad:
        print(f"Pad {args.from_pad} not found")
        sys.exit(1)
    if not dst_pad:
        print(f"Pad {args.to_pad} not found")
        sys.exit(1)

    src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)
    source = (src_pad["x"], src_pad["y"])
    dest = (dst_pad["x"], dst_pad["y"])

    print(f"\nQuery: {args.net}")
    print(f"  {src_pad['ref']}.{src_pad['pin']} ({source[0]}, {source[1]})")
    print(f"  {dst_pad['ref']}.{dst_pad['pin']} ({dest[0]}, {dest[1]})")
    print(f"  Length: {math.hypot(dest[0]-source[0], dest[1]-source[1]):.2f}mm")

    candidates = retrieve_and_transform(
        collection, pads, tracks, args.net, source, dest, n=args.n
    )

    print(f"\n{'=' * 60}")
    print(f"TRANSFORMED CANDIDATES")
    print(f"{'=' * 60}")
    print_candidates(candidates)

    if candidates:
        print(f"\n{'=' * 60}")
        print(f"BEST CANDIDATE SEGMENTS")
        print(f"{'=' * 60}")
        best = candidates[0]
        for j, seg in enumerate(best["segments"]):
            print(f"  [{j}] ({seg[0]}, {seg[1]}) -> ({seg[2]}, {seg[3]}) {seg[4]}")
        if best["vias"]:
            print(f"  Vias:")
            for v in best["vias"]:
                print(f"    ({v[0]}, {v[1]})")