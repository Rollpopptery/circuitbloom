#!/usr/bin/env python3
"""
inspect_routes.py — Find and inspect routes with suspicious length ratios.

Usage:
    python3 inspect_routes.py route_collection
"""

import json
import math
import sys

import chromadb


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_routes.py <path_to_route_collection>")
        sys.exit(1)

    db_path = sys.argv[1]
    print(f"Opening: {db_path}")

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection("pcb_routes")
    total = collection.count()
    print(f"Total routes: {total}")

    # Fetch all in batches
    all_meta = []
    batch = 5000
    offset = 0
    while offset < total:
        r = collection.get(limit=batch, offset=offset, include=["metadatas"])
        all_meta.extend(r["metadatas"])
        offset += len(r["metadatas"])

    print(f"Fetched {len(all_meta)} routes")

    # Find sub-1.0 ratio routes
    print(f"\n{'=' * 70}")
    print("ROUTES WITH RATIO < 1.0 (should be impossible)")
    print(f"{'=' * 70}")

    suspicious = []
    for m in all_meta:
        from_x = m.get("from_x", 0)
        from_y = m.get("from_y", 0)
        to_x = m.get("to_x", 0)
        to_y = m.get("to_y", 0)
        length = m.get("length_mm", 0)

        straight = math.hypot(to_x - from_x, to_y - from_y)
        if straight < 0.5 or length <= 0:
            continue

        ratio = length / straight
        if ratio < 1.0:
            suspicious.append((ratio, m, straight))

    suspicious.sort(key=lambda x: x[0])

    if not suspicious:
        print("  None found!")
    else:
        print(f"  Found {len(suspicious)} routes with ratio < 1.0\n")

        for ratio, m, straight in suspicious[:10]:
            segments = json.loads(m.get("segments", "[]"))

            print(f"  Board: {m.get('board', '?')}")
            print(f"  {m.get('from_ref')}.{m.get('from_pin')} -> {m.get('to_ref')}.{m.get('to_pin')}")
            print(f"  Net: {m.get('net', '?')}")
            print(f"  from_pt: ({m['from_x']}, {m['from_y']})")
            print(f"  to_pt:   ({m['to_x']}, {m['to_y']})")
            print(f"  straight_line: {straight:.3f} mm")
            print(f"  stored_length: {m['length_mm']} mm")
            print(f"  ratio: {ratio:.4f}")
            print(f"  layers: {m.get('layers', '?')}")
            print(f"  n_vias: {m.get('n_vias', 0)}")

            # Recompute length from segments
            seg_total = 0
            for s in segments:
                seg_total += math.hypot(s[2] - s[0], s[3] - s[1])

            print(f"  recomputed segment length: {seg_total:.3f} mm")

            # Check endpoints vs segment endpoints
            if segments:
                first = segments[0]
                last = segments[-1]
                print(f"  first segment start: ({first[0]}, {first[1]})")
                print(f"  last segment end:    ({last[2]}, {last[3]})")

                d_from = math.hypot(first[0] - m['from_x'], first[1] - m['from_y'])
                d_to = math.hypot(last[2] - m['to_x'], last[3] - m['to_y'])
                print(f"  from_pt to first seg start: {d_from:.3f} mm")
                print(f"  to_pt to last seg end:      {d_to:.3f} mm")

            print(f"  segments ({len(segments)}):")
            for j, s in enumerate(segments):
                layer = s[4] if len(s) > 4 else "?"
                seg_len = math.hypot(s[2] - s[0], s[3] - s[1])
                print(f"    [{j}] ({s[0]}, {s[1]}) -> ({s[2]}, {s[3]}) layer={layer} len={seg_len:.3f}")

            print()

    # Also show a few normal routes for comparison
    print(f"\n{'=' * 70}")
    print("SAMPLE NORMAL ROUTES (ratio 1.05 - 1.15)")
    print(f"{'=' * 70}\n")

    normal_count = 0
    for m in all_meta:
        from_x = m.get("from_x", 0)
        from_y = m.get("from_y", 0)
        to_x = m.get("to_x", 0)
        to_y = m.get("to_y", 0)
        length = m.get("length_mm", 0)

        straight = math.hypot(to_x - from_x, to_y - from_y)
        if straight < 0.5 or length <= 0:
            continue

        ratio = length / straight
        if 1.05 < ratio < 1.15:
            segments = json.loads(m.get("segments", "[]"))

            print(f"  Board: {m.get('board', '?')}")
            print(f"  {m.get('from_ref')}.{m.get('from_pin')} -> {m.get('to_ref')}.{m.get('to_pin')}")
            print(f"  from_pt: ({m['from_x']}, {m['from_y']})")
            print(f"  to_pt:   ({m['to_x']}, {m['to_y']})")
            print(f"  straight: {straight:.3f}  stored: {m['length_mm']}  ratio: {ratio:.4f}")
            print(f"  segments: {len(segments)}  vias: {m.get('n_vias', 0)}")
            print()

            normal_count += 1
            if normal_count >= 5:
                break


if __name__ == "__main__":
    main()