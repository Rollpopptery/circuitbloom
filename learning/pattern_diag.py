#!/usr/bin/env python3
"""
pattern_diag.py — Diagnostics on the trace pattern ChromaDB collection.

Shows stats, board coverage, similarity analysis, and sample queries.

Usage:
    python3 pattern_diag.py [--db path/to/trace_pattern_collection]
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import chromadb


def main():
    parser = argparse.ArgumentParser(description="Trace pattern database diagnostics")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "trace_pattern_collection"),
        help="Path to ChromaDB persistent storage")
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    print(f"Opening: {db_path}")

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_collection("trace_patterns")
    total = collection.count()
    print(f"Total patterns: {total}")

    if total == 0:
        print("Empty collection.")
        return

    # Fetch all metadata
    print("Fetching metadata...")
    all_meta = []
    batch = 5000
    offset = 0
    while offset < total:
        r = collection.get(limit=batch, offset=offset, include=["metadatas"])
        all_meta.extend(r["metadatas"])
        offset += len(r["metadatas"])

    # ============================================================
    # BOARD COVERAGE
    # ============================================================
    print(f"\n{'=' * 60}")
    print("BOARD COVERAGE")
    print(f"{'=' * 60}")

    boards = defaultdict(int)
    for m in all_meta:
        boards[m.get("board", "unknown")] += 1

    for board, count in sorted(boards.items(), key=lambda x: -x[1]):
        print(f"  {board:<45} {count:>5} patterns")

    print(f"\n  Total boards: {len(boards)}")
    print(f"  Total patterns: {total}")
    print(f"  Average per board: {total / len(boards):.0f}")

    # ============================================================
    # ROUTE TYPE DISTRIBUTION
    # ============================================================
    print(f"\n{'=' * 60}")
    print("ROUTE TYPES")
    print(f"{'=' * 60}")

    types = defaultdict(int)
    for m in all_meta:
        types[m.get("type", "unknown")] += 1

    for t, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {t:<30} {count:>5}")

    # ============================================================
    # TRACE LENGTH DISTRIBUTION
    # ============================================================
    print(f"\n{'=' * 60}")
    print("TRACE LENGTH DISTRIBUTION")
    print(f"{'=' * 60}")

    lengths = [m.get("trace_len_mm", 0) for m in all_meta]
    lengths = [l for l in lengths if l > 0]

    if lengths:
        print(f"  Min:    {min(lengths):.2f} mm")
        print(f"  Max:    {max(lengths):.2f} mm")
        print(f"  Mean:   {np.mean(lengths):.2f} mm")
        print(f"  Median: {np.median(lengths):.2f} mm")

        # Histogram buckets
        buckets = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 50), (50, 200)]
        print(f"\n  Length distribution:")
        for lo, hi in buckets:
            count = sum(1 for l in lengths if lo <= l < hi)
            bar = '#' * (count // max(1, total // 50))
            print(f"    {lo:>3}-{hi:<3} mm: {count:>5}  {bar}")

    # ============================================================
    # LAYER USAGE
    # ============================================================
    print(f"\n{'=' * 60}")
    print("LAYER USAGE")
    print(f"{'=' * 60}")

    layers = defaultdict(int)
    for m in all_meta:
        for layer in m.get("layers", "").split(","):
            if layer:
                layers[layer] += 1

    for layer, count in sorted(layers.items(), key=lambda x: -x[1]):
        print(f"  {layer:<20} {count:>5}")

    # ============================================================
    # VIA DISTRIBUTION
    # ============================================================
    print(f"\n{'=' * 60}")
    print("VIA DISTRIBUTION")
    print(f"{'=' * 60}")

    via_counts = defaultdict(int)
    for m in all_meta:
        n = m.get("n_vias", 0)
        via_counts[n] += 1

    for n, count in sorted(via_counts.items()):
        label = f"{n} vias" if n != 1 else "1 via"
        print(f"  {label:<15} {count:>5}")

    # ============================================================
    # TOP COMPONENTS (by pattern count)
    # ============================================================
    print(f"\n{'=' * 60}")
    print("TOP SOURCE COMPONENTS (by pattern count)")
    print(f"{'=' * 60}")

    from_refs = defaultdict(int)
    for m in all_meta:
        ref = m.get("from_ref", "")
        if ref:
            from_refs[ref] += 1

    for ref, count in sorted(from_refs.items(), key=lambda x: -x[1])[:20]:
        print(f"  {ref:<20} {count:>5}")

    # ============================================================
    # SAMPLE SIMILARITY TEST
    # ============================================================
    print(f"\n{'=' * 60}")
    print("SAMPLE SIMILARITY TEST")
    print(f"{'=' * 60}")

    # Pick a random pattern and find its nearest neighbours
    sample_size = min(100, total)
    sample = collection.get(limit=sample_size, include=["metadatas", "embeddings"])

    if len(sample["embeddings"]) >= 2:
        embeddings = np.array(sample["embeddings"])
        # Normalise
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        embeddings = embeddings / norms

        # Pick first pattern as query
        query_idx = 0
        query_meta = sample["metadatas"][query_idx]
        query_vec = embeddings[query_idx]

        # Compute similarities
        sims = embeddings @ query_vec
        ranked = sorted(enumerate(sims), key=lambda x: -x[1])

        print(f"\n  Query: {query_meta.get('board', '?')} "
              f"{query_meta.get('net', '?')} "
              f"{query_meta.get('from_ref', '?')}.{query_meta.get('from_pin', '?')} -> "
              f"{query_meta.get('to_ref', '?')}.{query_meta.get('to_pin', '?')}")
        print(f"  Trace: {query_meta.get('trace_len_mm', '?')}mm "
              f"{query_meta.get('layers', '?')} "
              f"vias={query_meta.get('n_vias', '?')}")

        print(f"\n  Top 5 matches (excluding self):")
        shown = 0
        for idx, sim in ranked:
            if idx == query_idx:
                continue
            m = sample["metadatas"][idx]
            print(f"    sim={sim:.4f}  [{m.get('board', '?')}] "
                  f"{m.get('net', '?')} "
                  f"{m.get('from_ref', '?')}.{m.get('from_pin', '?')} -> "
                  f"{m.get('to_ref', '?')}.{m.get('to_pin', '?')} "
                  f"({m.get('trace_len_mm', '?')}mm)")
            shown += 1
            if shown >= 5:
                break

        # Overall similarity stats for sample
        pair_sims = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                pair_sims.append(sims[j] if i == 0 else (embeddings[i] @ embeddings[j]))

        if pair_sims:
            print(f"\n  Pairwise similarity stats (sample of {sample_size}):")
            print(f"    Min:   {min(pair_sims):.4f}")
            print(f"    Max:   {max(pair_sims):.4f}")
            print(f"    Mean:  {np.mean(pair_sims):.4f}")
            print(f"    Range: {max(pair_sims) - min(pair_sims):.4f}")


if __name__ == "__main__":
    main()