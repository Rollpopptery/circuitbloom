#!/usr/bin/env python3
"""
query_patterns.py — Query the trace pattern database.

Supports two modes:
  1. Direct pad-to-pad query (for unrouted boards):
     python3 query_patterns.py --from-pad XU1.18 --to-pad U4.12 --net SPI_SCK

  2. Auto-pick from existing routes:
     python3 query_patterns.py

Usage:
    python3 query_patterns.py --from-pad REF.PIN --to-pad REF.PIN --net NET
    python3 query_patterns.py [--net NET]
    python3 query_patterns.py --save-query query.png
"""

import argparse
import json
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from rebuild_routes import rebuild_routes
from pad_pattern_render import (
    render_pad_pattern,
    count_component_pins,
    count_visible_obstacles,
    choose_source_dest,
    render_and_save,
)
from trace_patterndb import search_patterns, encode_image, _load_dino

import chromadb

SERVER = "http://localhost:8084"


def find_pad(pads, ref_pin):
    """Find a pad by REF.PIN string (e.g. 'XU1.18')."""
    parts = ref_pin.split(".", 1)
    if len(parts) != 2:
        return None
    ref, pin = parts
    for p in pads:
        if p["ref"] == ref and p["pin"] == pin:
            return p
    return None


def main():
    parser = argparse.ArgumentParser(description="Query trace pattern database")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "trace_pattern_collection"),
        help="ChromaDB persist directory")
    parser.add_argument("--from-pad", default=None,
        help="Source pad (e.g. XU1.18)")
    parser.add_argument("--to-pad", default=None,
        help="Destination pad (e.g. U4.12)")
    parser.add_argument("--net", default=None,
        help="Net name")
    parser.add_argument("--n", type=int, default=10,
        help="Number of results")
    parser.add_argument("--save-query", default=None,
        help="Save query pattern image to this path")
    args = parser.parse_args()

    # Load DINOv2
    print("Loading DINOv2...")
    _load_dino()

    # Open collection
    db_path = os.path.abspath(args.db)
    print(f"Opening collection: {db_path}")
    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection("trace_patterns")
    print(f"Collection has {collection.count()} patterns")

    # Fetch current board state
    print("\nFetching board state...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    tracks = data.get("tracks", [])
    pads = data.get("pads", [])
    vias = data.get("vias", [])
    print(f"  {len(tracks)} tracks, {len(pads)} pads, {len(vias)} vias")

    pad_counts = count_component_pins(pads)

    # ============================================================
    # MODE 1: Direct pad-to-pad query
    # ============================================================
    if args.from_pad and args.to_pad:
        src_pad = find_pad(pads, args.from_pad)
        if not src_pad:
            print(f"Pad {args.from_pad} not found")
            return

        dst_pad = find_pad(pads, args.to_pad)
        if not dst_pad:
            print(f"Pad {args.to_pad} not found")
            return

        # Use provided net or get it from the pad
        net = args.net or src_pad.get("net", "")
        if not net:
            print("Could not determine net. Use --net to specify.")
            return

        # Apply consistent source/dest ordering
        src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)

        source = (src_pad["x"], src_pad["y"])
        dest = (dst_pad["x"], dst_pad["y"])
        trace_len = math.hypot(dest[0] - source[0], dest[1] - source[1])
        n_obs = count_visible_obstacles(pads, net, source, dest)

    # ============================================================
    # MODE 2: Auto-pick from existing routes
    # ============================================================
    else:
        routes = rebuild_routes(tracks, pads, vias)

        if args.net:
            candidates = []
            for r in routes:
                if r["net"] != args.net:
                    continue
                if not r.get("from_pad") or not r.get("to_pad"):
                    continue
                sp, dp = choose_source_dest(
                    r.get("from_pad"), r.get("to_pad"), pad_counts)
                if not sp or not dp:
                    continue
                s = (sp["x"], sp["y"])
                d = (dp["x"], dp["y"])
                tl = math.hypot(d[0] - s[0], d[1] - s[1])
                no = count_visible_obstacles(pads, r["net"], s, d)
                candidates.append((no, tl, r, sp, dp))
        else:
            candidates = []
            for r in routes:
                if not r.get("from_pad") or not r.get("to_pad"):
                    continue
                sp, dp = choose_source_dest(
                    r.get("from_pad"), r.get("to_pad"), pad_counts)
                if not sp or not dp:
                    continue
                s = (sp["x"], sp["y"])
                d = (dp["x"], dp["y"])
                tl = math.hypot(d[0] - s[0], d[1] - s[1])
                if tl < 2.0:
                    continue
                no = count_visible_obstacles(pads, r["net"], s, d)
                if no >= 3:
                    candidates.append((no, tl, r, sp, dp))

        candidates.sort(key=lambda x: (-x[0], -x[1]))

        if not candidates:
            print("No suitable routes found for querying.")
            return

        n_obs, trace_len, route, src_pad, dst_pad = candidates[0]
        source = (src_pad["x"], src_pad["y"])
        dest = (dst_pad["x"], dst_pad["y"])
        net = route["net"]

    # ============================================================
    # DISPLAY QUERY
    # ============================================================
    print(f"\n{'=' * 60}")
    print(f"QUERY")
    print(f"{'=' * 60}")
    print(f"  Net:       {net}")
    print(f"  From:      {src_pad['ref']}.{src_pad['pin']} at ({source[0]}, {source[1]})")
    print(f"  To:        {dst_pad['ref']}.{dst_pad['pin']} at ({dest[0]}, {dest[1]})")
    print(f"  Length:    {trace_len:.2f} mm")
    print(f"  Obstacles: {n_obs} pads in window")

    # Save query image
    if args.save_query:
        render_and_save(pads, net, source, dest, args.save_query)
        print(f"  Image:     {args.save_query}")

    # ============================================================
    # SEARCH
    # ============================================================
    print(f"\n{'=' * 60}")
    print(f"RESULTS (top {args.n})")
    print(f"{'=' * 60}")

    results = search_patterns(collection, pads, net, source, dest, n=args.n)

    for i, (meta, dist) in enumerate(results):
        board = meta.get("board", "?")
        m_net = meta.get("net", "?")
        m_from = f"{meta.get('from_ref', '?')}.{meta.get('from_pin', '?')}"
        m_to = f"{meta.get('to_ref', '?')}.{meta.get('to_pin', '?')}"
        m_len = meta.get("trace_len_mm", 0)
        m_layers = meta.get("layers", "?")
        m_vias = meta.get("n_vias", 0)
        m_segs = meta.get("n_segments", 0)

        print(f"\n  [{i+1}] dist={dist:.4f}  [{board}]")
        print(f"      {m_net}  {m_from} -> {m_to}")
        print(f"      {m_len}mm  {m_layers}  vias={m_vias}  segs={m_segs}")

    # ============================================================
    # SUMMARY
    # ============================================================
    dists = [d for _, d in results]
    if dists:
        print(f"\n{'=' * 60}")
        print(f"MATCH QUALITY")
        print(f"{'=' * 60}")
        print(f"  Best:  {min(dists):.4f}")
        print(f"  Worst: {max(dists):.4f}")
        print(f"  Mean:  {sum(dists)/len(dists):.4f}")


if __name__ == "__main__":
    main()