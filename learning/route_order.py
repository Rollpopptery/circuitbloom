#!/usr/bin/env python3
"""
route_ratio_analysis.py — Analyse actual vs ideal route length ratios
from the ChromaDB pcb_routes collection.

For each route involving a component with 3+ routed pins, computes:
    ratio = actual_length / straight_line_distance

Groups results by footprint and pin to reveal which pins historically
get clean paths (low ratio = routed early) vs detoured paths (high ratio
= routed late). This encodes implicit routing priority from the design corpus.

Usage:
    python3 route_ratio_analysis.py [--db PATH] [--min-pins N]

    --db:       path to ChromaDB persistent storage
                (default: ../router/route_collection)
    --min-pins: minimum routed pin count to include a component (default: 3)
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import chromadb


def load_collection(db_path, collection_name="pcb_routes"):
    """Open ChromaDB and return the collection."""
    client = chromadb.PersistentClient(path=db_path)
    return client.get_collection(name=collection_name)


def fetch_all_routes(collection):
    """Fetch all routes from the collection in batches."""
    total = collection.count()
    print(f"Collection has {total} routes")

    all_metadatas = []
    batch_size = 5000
    offset = 0

    while offset < total:
        results = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"]
        )
        all_metadatas.extend(results["metadatas"])
        offset += len(results["metadatas"])
        print(f"  Fetched {offset}/{total}")

    return all_metadatas


def count_pins_per_component(metadatas):
    """Count distinct routed pins per component per board.

    Returns:
        dict of (board, ref) -> set of pin identifiers
    """
    pin_counts = defaultdict(set)

    for meta in metadatas:
        board = meta.get("board", "")

        from_ref = meta.get("from_ref", "")
        from_pin = meta.get("from_pin", "")
        if from_ref and from_pin:
            pin_counts[(board, from_ref)].add(from_pin)

        to_ref = meta.get("to_ref", "")
        to_pin = meta.get("to_pin", "")
        if to_ref and to_pin:
            pin_counts[(board, to_ref)].add(to_pin)

    return pin_counts


_PASSIVE_PATTERNS = [
    "R_", "C_", "L_", "D_", "LED_", "CP_",
    "0402", "0603", "0805", "1206", "1210", "0201",
    "Resistor", "Capacitor", "Inductor", "Diode",
    "Ferrite", "Fuse", "Varistor", "Thermistor",
    "SOD-", "SMA_", "SMB_", "SMC_",
    "MiniMELF", "MELF",
    "TestPoint", "MountingHole", "Fiducial",
    "SolderJumper", "Logo", "kibuzzard",
]

def _is_passive_footprint(footprint):
    """Check if a footprint looks like a 2-pin passive component."""
    if not footprint:
        return False
    # Check the short name after the library prefix
    name = footprint.split(":")[-1] if ":" in footprint else footprint
    return any(name.startswith(p) or p in name for p in _PASSIVE_PATTERNS)


def compute_ratios(metadatas, multi_pin_components, min_straight_line=0.5):
    """Compute actual/ideal length ratios for routes involving multi-pin components.

    Uses actual segment endpoints (track start/end) rather than pad centres
    to avoid false sub-1.0 ratios caused by pad inset.

    Args:
        metadatas: list of route metadata dicts
        multi_pin_components: set of (board, ref) tuples with 3+ pins
        min_straight_line: minimum straight line distance in mm to avoid
                          division by near-zero (default: 0.5mm)

    Returns:
        list of dicts with route info and ratio
    """
    results = []

    for meta in metadatas:
        board = meta.get("board", "")
        from_ref = meta.get("from_ref", "")
        to_ref = meta.get("to_ref", "")

        # At least one endpoint must be a multi-pin component
        from_is_multi = (board, from_ref) in multi_pin_components
        to_is_multi = (board, to_ref) in multi_pin_components

        if not (from_is_multi or to_is_multi):
            continue

        # Skip 2-pin passives that slipped through (resistors, caps, etc.)
        from_fp = meta.get("from_footprint", "")
        to_fp = meta.get("to_footprint", "")
        if from_is_multi and _is_passive_footprint(from_fp):
            from_is_multi = False
        if to_is_multi and _is_passive_footprint(to_fp):
            to_is_multi = False
        if not (from_is_multi or to_is_multi):
            continue

        # Must be a pin-to-pin route (not a junction)
        if not from_ref or not to_ref:
            continue

        # Parse segments to get actual track endpoints
        segments = json.loads(meta.get("segments", "[]"))
        if not segments:
            continue

        length_mm = meta.get("length_mm", 0)
        if length_mm <= 0:
            continue

        # Use actual segment endpoints, not pad centres
        first_seg = segments[0]
        last_seg = segments[-1]
        from_x = first_seg[0]
        from_y = first_seg[1]
        to_x = last_seg[2]
        to_y = last_seg[3]

        straight_line = math.hypot(to_x - from_x, to_y - from_y)

        if straight_line < min_straight_line:
            continue

        # Recompute length from segments for accuracy
        length_mm = sum(
            math.hypot(s[2] - s[0], s[3] - s[1]) for s in segments
        )

        ratio = length_mm / straight_line

        # Determine which end is the multi-pin component (the "dominant" end)
        # If both are multi-pin, record from both perspectives
        if from_is_multi:
            results.append({
                "board": board,
                "ref": from_ref,
                "pin": meta.get("from_pin", ""),
                "footprint": meta.get("from_footprint", ""),
                "value": meta.get("from_value", ""),
                "other_ref": to_ref,
                "other_pin": meta.get("to_pin", ""),
                "length_mm": length_mm,
                "straight_line_mm": round(straight_line, 3),
                "ratio": round(ratio, 3),
                "n_vias": meta.get("n_vias", 0),
                "layers": meta.get("layers", ""),
                "net": meta.get("net", ""),
            })

        if to_is_multi and to_ref != from_ref:
            results.append({
                "board": board,
                "ref": to_ref,
                "pin": meta.get("to_pin", ""),
                "footprint": meta.get("to_footprint", ""),
                "value": meta.get("to_value", ""),
                "other_ref": from_ref,
                "other_pin": meta.get("from_pin", ""),
                "length_mm": length_mm,
                "straight_line_mm": round(straight_line, 3),
                "ratio": round(ratio, 3),
                "n_vias": meta.get("n_vias", 0),
                "layers": meta.get("layers", ""),
                "net": meta.get("net", ""),
            })

    return results


def group_by_value_and_pin(results):
    """Group ratio results by component value and pin.

    Returns:
        dict of value -> pin -> list of ratios
    """
    grouped = defaultdict(lambda: defaultdict(list))

    for r in results:
        value = r.get("value", "")
        if not value:
            continue
        pin = r["pin"]
        grouped[value][pin].append(r["ratio"])

    return grouped


def print_report(grouped, all_results, pin_counts, min_pins):
    """Print analysis report."""

    # Summary
    print(f"\n{'=' * 70}")
    print(f"ROUTE RATIO ANALYSIS")
    print(f"{'=' * 70}")

    # Component stats
    total_components = len(pin_counts)
    multi_pin = {k: v for k, v in pin_counts.items() if len(v) >= min_pins}
    print(f"\nComponents in corpus: {total_components}")
    print(f"Components with {min_pins}+ pins: {len(multi_pin)}")
    print(f"Routes analysed: {len(all_results)}")

    if not all_results:
        print("No routes to analyse.")
        return

    # Overall ratio stats
    ratios = [r["ratio"] for r in all_results]
    print(f"\nOverall ratio stats:")
    print(f"  Min:    {min(ratios):.3f}")
    print(f"  Max:    {max(ratios):.3f}")
    print(f"  Mean:   {sum(ratios) / len(ratios):.3f}")
    ratios_sorted = sorted(ratios)
    print(f"  Median: {ratios_sorted[len(ratios_sorted) // 2]:.3f}")

    # Per component value summary
    print(f"\n{'=' * 70}")
    print(f"PER COMPONENT SUMMARY (sorted by mean ratio)")
    print(f"{'=' * 70}")

    val_stats = {}
    for val, pins in grouped.items():
        all_val_ratios = []
        for pin, pin_ratios in pins.items():
            all_val_ratios.extend(pin_ratios)
        if all_val_ratios:
            val_stats[val] = {
                "mean": sum(all_val_ratios) / len(all_val_ratios),
                "count": len(all_val_ratios),
                "n_pins": len(pins),
            }

    for val, stats in sorted(val_stats.items(), key=lambda x: x[1]["mean"]):
        print(f"\n  {val}")
        print(f"    Pins: {stats['n_pins']}  Routes: {stats['count']}  Mean ratio: {stats['mean']:.3f}")

    # Detailed per-pin breakdown for top components (by route count)
    print(f"\n{'=' * 70}")
    print(f"PIN-LEVEL DETAIL (top components by route count)")
    print(f"{'=' * 70}")

    top_values = sorted(val_stats.keys(),
                        key=lambda v: val_stats[v]["count"],
                        reverse=True)[:15]

    for val in top_values:
        pins = grouped[val]
        print(f"\n  {val} ({val_stats[val]['count']} routes, {val_stats[val]['n_pins']} pins)")
        print(f"  {'-' * 60}")

        # Sort pins by mean ratio (lowest = likely routed first)
        pin_means = {}
        for pin, pin_ratios in pins.items():
            pin_means[pin] = sum(pin_ratios) / len(pin_ratios)

        print(f"  {'Pin':<10} {'Mean':>8} {'Min':>8} {'Max':>8} {'Count':>6}  Priority")
        print(f"  {'---':<10} {'----':>8} {'---':>8} {'---':>8} {'-----':>6}  --------")

        for pin, mean in sorted(pin_means.items(), key=lambda x: x[1]):
            pin_ratios = pins[pin]
            mn = min(pin_ratios)
            mx = max(pin_ratios)
            count = len(pin_ratios)

            if mean < 1.2:
                priority = "HIGH (clean path)"
            elif mean < 1.5:
                priority = "MEDIUM"
            elif mean < 2.0:
                priority = "LOW"
            else:
                priority = "FLEXIBLE (tolerant)"

            print(f"  {pin:<10} {mean:>8.3f} {mn:>8.3f} {mx:>8.3f} {count:>6}  {priority}")

    # Routing order suggestion
    print(f"\n{'=' * 70}")
    print(f"SUGGESTED ROUTING ORDER (all components, by mean ratio)")
    print(f"{'=' * 70}")
    print(f"Route pins with lowest ratio first — they historically get clean paths.\n")

    all_pin_stats = []
    for val, pins in grouped.items():
        for pin, pin_ratios in pins.items():
            mean = sum(pin_ratios) / len(pin_ratios)
            all_pin_stats.append({
                "value": val,
                "pin": pin,
                "mean_ratio": mean,
                "count": len(pin_ratios),
            })

    for i, ps in enumerate(sorted(all_pin_stats, key=lambda x: x["mean_ratio"])[:50]):
        print(f"  {i+1:>3}. {ps['value']} pin {ps['pin']}"
              f"  ratio={ps['mean_ratio']:.3f}  (n={ps['count']})")


def main():
    parser = argparse.ArgumentParser(description="Analyse route length ratios")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "..", "router", "route_collection"),
        help="Path to ChromaDB persistent storage")
    parser.add_argument("--min-pins", type=int, default=3,
        help="Minimum routed pins for a component to be included")
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    print(f"Opening ChromaDB at: {db_path}")

    collection = load_collection(db_path)
    metadatas = fetch_all_routes(collection)

    print(f"\nCounting pins per component...")
    pin_counts = count_pins_per_component(metadatas)

    multi_pin = {k for k, v in pin_counts.items() if len(v) >= args.min_pins}
    print(f"  {len(pin_counts)} total components")
    print(f"  {len(multi_pin)} with {args.min_pins}+ routed pins")

    print(f"\nComputing ratios...")
    results = compute_ratios(metadatas, multi_pin)
    print(f"  {len(results)} routes analysed")

    grouped = group_by_value_and_pin(results)

    print_report(grouped, results, pin_counts, args.min_pins)


if __name__ == "__main__":
    main()