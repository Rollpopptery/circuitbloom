# PCB VERIFICATION
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline


## OVERVIEW

All checks must pass before generating a .kicad_pcb.
They are run via check_dpcb.py — the AI cannot skip them or
do them by inspection. Run it. Read the output. Fix violations.
Run again. Do not proceed to gen_pcb.py until clean.

```bash
python3 check_dpcb.py design.dpcb
```

Exit code 0 = clean. Exit code 1 = violations found.


## CHECKS PACKAGE (utilities/checks/)

### dpcb_parser.py
Shared parser used by all checks. Parses .dpcb files and computes
absolute pad positions from FP positions + PADS offsets + rotation.
All checks receive: tracks, pad_positions, nets.

### pad_conflicts.py — Pad Conflict (trace-vs-pad)
For every trace segment, for every through-hole pad on a different
net: compute minimum distance from pad centre to trace segment.
If distance < pad_radius + clearance, it is a violation.

For 1.6mm diameter TH pads with 0.2mm clearance:
  hit_distance = 0.8 + 0.2 = 1.0mm

Through-hole pads exist on ALL copper layers. A B.Cu trace
passing through a TH pad is a short circuit, same as F.Cu.

THIS IS THE CRITICAL CHECK. Without it, traces route through
IC bodies, shorting VCC to GND.

### crossings.py — Same-Layer Crossing (trace-vs-trace)
For every pair of trace segments on the same layer with different
nets: check if a horizontal segment's y-value falls within a
vertical segment's y-range AND the vertical segment's x-value
falls within the horizontal's x-range.

Only checks orthogonal crossings.

### collinear.py — Collinear Conflict
For every pair of trace segments on the same layer with different
nets: check if they run along the same line (same x for verticals,
same y for horizontals) with overlapping ranges.

Catches traces that merge into the same path — an invisible short.

### connectivity.py — Net Connectivity
Verifies that all pads assigned to a net are reachable via tracks
and vias. Catches unconnected pads (missing routes).

### dangling.py — Dangling Segment Endpoints
Checks for trace endpoints that don't connect to a pad or another
trace. Board edge endpoints are excepted.

### mitre.py — Mitre/Chamfer Geometry Helper
Not a check — a geometry utility used by the AI polish pass to
compute 45° chamfer coordinates before inserting them into .dpcb.


## RUNNING INDIVIDUAL CHECKS

```bash
python3 check_dpcb.py design.dpcb --check pads
python3 check_dpcb.py design.dpcb --check crossings
python3 check_dpcb.py design.dpcb --check collinear
python3 check_dpcb.py design.dpcb --check connectivity
python3 check_dpcb.py design.dpcb --verbose   # also prints pad positions table
```


## PRIORITY OF PROBLEMS (WORST-FIRST)

1. Short circuits (trace through wrong-net pad)
2. Same-layer crossings
3. Collinear conflicts
4. Unconnected nets
5. Dangling endpoints
6. DRC violations (clearance, etc.)
7. Unnecessarily long routes
8. Aesthetics


## AI POLISH PASS

After all checks pass, before running gen_pcb.py, apply
aesthetic improvements:

- Replace hard 90° corners with 45° chamfers where space permits
- Minimum chamfer segment length: 0.5mm
- Do not chamfer where space is tight or where it would move a
  track endpoint away from a pad
- Where space permits, extend chamfers into full mitres —
  diagonal length limited by adjacent geometry
- Use mitre.py for chamfer coordinate calculation

After polishing, re-run check_dpcb.py to confirm no violations
were introduced.


## SCALE OF VERIFICATION

For a 7-component board with 39 trace segments and 20 pads:
- Pad conflict check: 39 × 20 = 780 distance calculations
- Crossing check: 39 × 38 / 2 = 741 pair checks
- Collinear check: same as crossing check

This cannot be done by inspection. Always run the scripts.
Typically takes 3-7 iterations to converge on a clean routing.


## POST-ROUTING OPTIMISATION HINTS

After all checks pass and the polish pass is complete, the following
hint procedures may be invoked. These are optional optimisation steps —
they do not gate gen_pcb.py. They suggest improvements only.

### HINT_ROTATE_ROUTED
Procedure: utilities/checks/HINT_ROTATE_ROUTED.md

Tests whether rotating a component (90°, 180°, 270°) would reduce
total routed trace length to immediate next connected pads.
Scope: components with 4 or fewer pads only.

To invoke, read the procedure file and follow steps 1–7 exactly,
announcing:

  "Invoking HINT_ROTATE_ROUTED for <component_ref>..."

Do not invoke during first-pass routing.
Do not invoke if any hard violations remain.
Do not apply a suggested rotation without explicit instruction.
After applying, re-run check_dpcb.py to confirm clean.
