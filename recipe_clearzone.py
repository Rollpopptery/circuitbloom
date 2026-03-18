#!/usr/bin/env python3
"""
recipe_clearzone.py — Show occupied y-values (tracks + vias) in an x-band of the dpcb.

Use this before recipe_sr_route.py to pick a safe approach-y with zero iteration.

Usage:
  python3 utilities/recipe_clearzone.py demo_2_7seg/demo7seg.dpcb --x1 30 --x2 38 --layer F.Cu
  python3 utilities/recipe_clearzone.py demo_2_7seg/demo7seg.dpcb --x1 8 --x2 16 --layer B.Cu

Output: sorted list of y-values occupied in that x-band, with net labels.
Pick an approach-y that does not appear in the list (and is >0.45mm from any listed value).
"""

import argparse
import re
import sys

CLEARANCE = 0.2
TRACK_HALF = 0.125
VIA_RADIUS = 0.6  # outer radius (drill 0.6 + annular 0.3 — but outer = drill/2 + annular = 0.3+0.3=0.6)

def segments_overlap(a1, a2, b1, b2, margin=0.0):
    return max(a1, b1) <= min(a2, b2) + margin

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('dpcb', help='Path to .dpcb file')
    p.add_argument('--x1', required=True, type=float, help='X-band left edge')
    p.add_argument('--x2', required=True, type=float, help='X-band right edge')
    p.add_argument('--layer', default='F.Cu', help='Layer to check (default F.Cu)')
    args = p.parse_args()

    x1, x2 = sorted([args.x1, args.x2])
    layer = args.layer

    occupied = []  # (y_min, y_max, y_label, net, kind)

    trk_re = re.compile(
        r'TRK:\(([^,]+),([^)]+)\)->\(([^,]+),([^)]+)\):([^:]+):([^:]+):(.+)'
    )
    via_re = re.compile(
        r'VIA:\(([^,]+),([^)]+)\):([^:]+):(.+)'
    )

    with open(args.dpcb) as f:
        for line in f:
            line = line.strip()

            m = trk_re.match(line)
            if m:
                x_a, y_a, x_b, y_b = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
                trk_layer = m.group(6)
                net = m.group(7)
                if trk_layer != layer:
                    continue
                tx1, tx2 = sorted([x_a, x_b])
                if not segments_overlap(tx1, tx2, x1, x2):
                    continue
                ty1, ty2 = sorted([y_a, y_b])
                occupied.append((ty1, ty2, f"y={ty1}..{ty2}" if ty1 != ty2 else f"y={ty1}", net, 'TRK'))
                continue

            m = via_re.match(line)
            if m:
                vx, vy = float(m.group(1)), float(m.group(2))
                net = m.group(4)
                if not segments_overlap(vx - VIA_RADIUS, vx + VIA_RADIUS, x1, x2):
                    continue
                occupied.append((vy - VIA_RADIUS, vy + VIA_RADIUS, f"y={vy}", net, 'VIA'))

    occupied.sort()

    print(f"\nOccupied y-values in x=[{x1},{x2}] layer={layer}:")
    print(f"{'Y-range':<28}  {'Net':<20}  Kind")
    print("-" * 60)
    if not occupied:
        print("  (none)")
    for (y1, y2, label, net, kind) in occupied:
        edge1 = y1 - TRACK_HALF if kind == 'TRK' else y1
        edge2 = y2 + TRACK_HALF if kind == 'TRK' else y2
        print(f"  {label:<26}  {net:<20}  {kind}  (edges {edge1:.3f}..{edge2:.3f})")

    print()
    min_gap = TRACK_HALF + CLEARANCE + TRACK_HALF  # 0.45mm min separation centre-to-centre for tracks
    print(f"Min safe separation from any track centre: {min_gap}mm")
    print(f"Min safe separation from any via edge: {CLEARANCE}mm")
    print()

if __name__ == '__main__':
    main()
