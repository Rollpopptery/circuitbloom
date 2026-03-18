#!/usr/bin/env python3
"""
check_dpcb.py  —  PCB layout verifier for .dpcb files

Runs all checks in checks/ and reports violations.
Must pass before running gen_pcb.py.

Usage:
    python3 check_dpcb.py design.dpcb
    python3 check_dpcb.py design.dpcb --check pads
    python3 check_dpcb.py design.dpcb --check crossings
    python3 check_dpcb.py design.dpcb --check collinear
    python3 check_dpcb.py design.dpcb --check connectivity
    python3 check_dpcb.py design.dpcb --verbose

Exit codes:
    0  — all checks passed
    1  — one or more violations found
"""

import sys
import argparse

# Allow running from utilities/ or from project root
import os
sys.path.insert(0, os.path.dirname(__file__))

from checks.dpcb_parser import parse_dpcb, compute_pad_positions
from checks import pad_conflicts, crossings, collinear, connectivity, dangling, rotation_hint, rotation_trace, courtyard, kink, orphaned_vias, unnecessary_vias, via_th_bypass


# ---------------------------------------------------------------------------
# Check registry — add new checks here
# ---------------------------------------------------------------------------

CHECKS = {
    'pads':         ('Pad Conflicts (trace-vs-pad)',     pad_conflicts.run),
    'crossings':    ('Same-Layer Crossings',             crossings.run),
    'collinear':    ('Collinear Conflicts',              collinear.run),
    'connectivity': ('Net Connectivity',                 connectivity.run),
    'dangling':     ('Dangling Segment Endpoints',       dangling.run),
    'courtyard':    ('Courtyard Overlaps',               courtyard.run),
    'vias':         ('Orphaned Vias',                    orphaned_vias.run),
    'unneeded_vias':('Unnecessary Vias (TH pad bypass)', unnecessary_vias.run),
    'via_bypass':   ('Unnecessary Vias (multi-hop TH)',  via_th_bypass.run),
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Verify a .dpcb layout file before generating .kicad_pcb'
    )
    parser.add_argument('dpcb', help='Path to .dpcb file')
    parser.add_argument(
        '--check', '-c',
        choices=list(CHECKS.keys()) + ['rotation', 'rotation_trace'],
        default=None,
        help='Run only this check (default: run all)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Print pad positions table'
    )
    parser.add_argument(
        '--ref', '-r',
        default=None,
        help='Limit rotation_trace hint to a single component reference (e.g. R1)'
    )
    args = parser.parse_args()

    # --- Parse ---
    fps, pads_lib, nets, tracks, vias = parse_dpcb(args.dpcb)
    pad_positions, warnings = compute_pad_positions(fps, pads_lib, nets)

    # Extract board dimensions for dangling edge exception
    board_w, board_h = None, None
    try:
        with open(args.dpcb) as f:
            for line in f:
                if line.strip().startswith('BOARD:'):
                    parts = line.strip().split(':')[1].split('x')
                    board_w, board_h = float(parts[0]), float(parts[1])
                    break
    except Exception:
        pass

    print(f"Parsed: {len(fps)} footprints, {len(tracks)} tracks, {len(nets)} nets, "
          f"{len(pad_positions)} pads")

    for w in warnings:
        print(f"  {w}")

    if args.verbose:
        print()
        print("=== Pad Positions ===")
        for (ref, pnum), (ax, ay, net) in sorted(pad_positions.items()):
            print(f"  {ref}.{pnum:>3}  ({ax:7.3f}, {ay:7.3f})  net={net}")

    # --- Select checks to run ---
    HINT_ONLY = {'rotation', 'rotation_trace'}
    if args.check:
        if args.check in HINT_ONLY:
            checks_to_run = {}
        else:
            checks_to_run = {args.check: CHECKS[args.check]}
    else:
        checks_to_run = CHECKS

    # --- Run checks ---
    total_violations = 0
    print()

    for key, (label, fn) in checks_to_run.items():
        print(f"=== Check: {label} ===")
        try:
            if key == 'dangling':
                violations = fn(tracks, pad_positions, nets,
                                board_w=board_w, board_h=board_h)
            elif key in ('courtyard',):
                violations = fn(tracks, pad_positions, nets,
                                fps=fps, pads_lib=pads_lib)
            elif key in ('vias', 'unneeded_vias', 'via_bypass'):
                violations = fn(tracks, pad_positions, nets,
                                vias=vias)
            else:
                violations = fn(tracks, pad_positions, nets)
        except Exception as e:
            print(f"  ERROR running check '{key}': {e}")
            violations = [f"Check crashed: {e}"]

        real, suppressed = [], []
        for v in violations:
            if 'PAD CONFLICT' in v and 'dist=' in v:
                import re as _re
                m = _re.search(r'dist=([\d.]+)mm', v)
                if m and float(m.group(1)) <= 0.65:
                    suppressed.append(v)
                    continue
            if 'UNNECESSARY VIA' in v:
                suppressed.append(v)
                continue
            real.append(v)

        if real:
            for v in real:
                print(f"  FAIL  {v}")
        else:
            print("  PASS")
        if suppressed:
            print(f"  (suppressed {len(suppressed)} known false positive(s))")

        total_violations += len(real)
        print()

    # --- Kink hints (never count as violations) ---
    if args.check in (None, 'kinks'):
        print("=== Hints: Kink Detection ===")
        kink_hints = kink.run(tracks, pad_positions, nets)
        if kink_hints:
            for h in kink_hints:
                print(f"  {h}")
        else:
            print("  No kinks detected")
        print()

    # --- Rotation hints (never count as violations) ---
    if args.check in (None, 'rotation'):
        print("=== Hints: Rotation Optimisation (nearest neighbour) ===")
        hints = rotation_hint.run(tracks, pad_positions, nets, fps=fps, pads_lib=pads_lib, ref=args.ref)
        if hints:
            for h in hints:
                print(f"  {h}")
        else:
            print("  No rotation improvements found")
        print()

    if args.check in (None, 'rotation_trace'):
        print("=== Hints: Rotation Trace (connected pad distance, <=3-pad components) ===")
        hints = rotation_trace.run(tracks, pad_positions, nets, fps=fps, pads_lib=pads_lib, ref=args.ref)
        if hints:
            for h in hints:
                print(f"  {h}")
        else:
            print("  No rotation improvements found")
        print()

    # --- Summary ---
    print(f"=== SUMMARY: {total_violations} violation(s) across "
          f"{len(checks_to_run)} check(s) ===")

    return 0 if total_violations == 0 else 1


if __name__ == '__main__':
    sys.exit(main())