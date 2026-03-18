#!/usr/bin/env python3
"""
run_drc_help1.py — Run KiCad DRC and show only routing-relevant violations.

Filters pre-existing noise by comparing against a saved baseline.
Baseline is generated from a fully-placed but unrouted board.

Usage:
    python3 utilities/run_drc_help1.py <file.kicad_pcb>              # run and compare to baseline
    python3 utilities/run_drc_help1.py <file.kicad_pcb> --baseline   # save current state as baseline

Baseline saved alongside the .kicad_pcb as <file>.drc_baseline.json
"""

import sys
import json
import subprocess
import tempfile
import os

# Always filtered — purely cosmetic, never routing-related
ALWAYS_NOISE = {
    'lib_footprint_issues',
    'silk_overlap',
    'silk_over_copper',
}

def run_kicad_drc(kicad_pcb_path):
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(
            ['kicad-cli', 'pcb', 'drc', kicad_pcb_path, '-o', tmp, '--format', 'json'],
            capture_output=True, text=True, check=True
        )
        with open(tmp) as f:
            return json.load(f)
    finally:
        os.unlink(tmp)

def violation_key(v):
    """Stable key for deduplicating violations across runs."""
    return (v['type'], v['description'])

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <file.kicad_pcb> [--baseline]")
        sys.exit(1)

    kicad_pcb_path = sys.argv[1]
    save_baseline = '--baseline' in sys.argv
    baseline_path = kicad_pcb_path.replace('.kicad_pcb', '.drc_baseline.json')

    print(f"Running KiCad DRC on {kicad_pcb_path} ...")
    data = run_kicad_drc(kicad_pcb_path)

    violations = data.get('violations', [])
    unconnected = data.get('unconnected_items', [])

    # Filter always-noise
    violations = [v for v in violations if v['type'] not in ALWAYS_NOISE]

    if save_baseline:
        baseline = {'violations': violations, 'unconnected_count': len(unconnected)}
        with open(baseline_path, 'w') as f:
            json.dump(baseline, f, indent=2)
        print(f"Baseline saved: {baseline_path}")
        print(f"  {len(violations)} violations, {len(unconnected)} unconnected recorded as baseline.")
        return 0

    # Load baseline if it exists
    baseline_keys = set()
    baseline_unconnected = 0
    if os.path.exists(baseline_path):
        with open(baseline_path) as f:
            baseline_data = json.load(f)
        baseline_keys = {violation_key(v) for v in baseline_data['violations']}
        baseline_unconnected = baseline_data.get('unconnected_count', 0)
        print(f"Baseline loaded: {len(baseline_keys)} known violations filtered out.")
    else:
        print(f"No baseline found ({baseline_path}) — showing all violations.")

    new_violations = [v for v in violations if violation_key(v) not in baseline_keys]
    new_unconnected = len(unconnected) - baseline_unconnected

    print(f"\n=== DRC Results ===")
    print(f"New routing violations: {len(new_violations)}")
    print(f"Unconnected (new):      {new_unconnected} ({len(unconnected)} total, {baseline_unconnected} baseline)")
    print()

    if new_violations:
        print("=== Routing Violations ===")
        for v in new_violations:
            print(f"  [{v['type']}] {v['description']}")
        print()
        print(f"RESULT: FAIL ({len(new_violations)} violation(s))")
        return 1
    else:
        print("RESULT: PASS — no new routing violations")
        return 0

if __name__ == '__main__':
    sys.exit(main())
