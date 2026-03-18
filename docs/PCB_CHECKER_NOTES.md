# PCB CHECKER NOTES
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline
#
# Known limitations, false positives, and design decisions for each checker.
# Read alongside PCB_LESSONS.md.


## CHECKER INVENTORY

### Hard violation checks (exit code 1 if any fail)
- pad_conflicts.py    — trace-vs-pad distance. CRITICAL. Run first.
- crossings.py        — same-layer orthogonal trace crossings
- collinear.py        — collinear same-layer same-line overlaps (invisible shorts)
- connectivity.py     — all net pads reachable via tracks/vias
- dangling.py         — track endpoints not connected to pad or other track
- courtyard.py        — component body bounding box overlaps
- orphaned_vias.py    — vias with no tracks on one or both layers
- unnecessary_vias.py — vias with single-hop track leading to TH pad (same net)
- via_th_bypass.py    — vias unnecessary due to TH pads reachable within N hops

### Hint checks (printed but never count as violations)
- rotation_hint.py    — suggests rotation to reduce Manhattan wirelength (nearest neighbour)
- rotation_trace.py   — suggests rotation to shorten connected pad distances (topology, <=3 pads)
- kink.py             — detects U-turns and same-direction jogs


## KNOWN LIMITATIONS

### courtyard.py
Uses pad bounding box + per-footprint-type body margin. Approximate only —
does not use actual KiCad courtyard geometry. May miss overlaps for unusual
footprints or produce false positives for tightly-packed deliberate layouts.
Body margins defined in BODY_MARGINS dict — update when adding new footprint types.

### rotation_hint.py
- pad1 offset is always (0,0), so rotation never moves pad1. For footprints
  where pad1 is the critical connection, hints will never trigger.
- Scores by Manhattan distance to nearest net neighbour only. Does not account
  for routing obstacles — a suggested rotation may be unroutable in practice.
- Always verify routing feasibility before applying a rotation hint.

### rotation_trace.py
- Only runs on footprints with <=3 pads (simple passives). Complex ICs excluded.
- Scores by sum of Manhattan distances to ALL directly connected net peers
  (full topology), not just nearest neighbour. Distinct from rotation_hint.py.
- pad1 at (0,0) still never moves — same limitation as rotation_hint.py.
- A component may already be in the optimal rotation for pad distances even if
  the actual routed trace has kinks (routing topology ≠ pad topology).
- Late-stage check: more meaningful after routing is established.

### kink.py
- False positives from star-topology power routing. A GND rail branching from
  a central spine (e.g. y=22 horizontal with vertical branches) is flagged as
  a same-direction jog. This is often routing-necessary to avoid IC pin rows.
- Does not check whether a simplified route would conflict with pads or crossings.
- Treat all kink hints as suggestions requiring human review, not auto-fixable.

### via_th_bypass.py
- Correctly identifies that TH pads are reachable within MAX_HOPS (default 3)
  on both sides of a via, but cannot determine whether a single-layer re-route
  is actually feasible (may cross pads or other nets).
- Increasing MAX_HOPS increases false positive rate.
- unnecessary_vias.py (single-hop only) has lower false positive rate — run it
  first and treat via_th_bypass results with more scepticism.

### dangling.py
- Board edge endpoints are excepted (deliberate).
- Does not detect T-junction mid-segment connections — a track ending at the
  midpoint of another track is not flagged. KiCad handles this natively but
  our checker requires explicit shared endpoints.

### connectivity.py
- Relies on track endpoints reaching pad positions within tolerance.
- Does not model T-junctions — a pad at a track midpoint may appear
  unconnected if no track endpoint coincides with the pad position.


## TUNING PARAMETERS

- pad_conflicts.py:    hit_distance = pad_radius + clearance (default 0.8 + 0.2 = 1.0mm)
- via_th_bypass.py:    MAX_HOPS = 3
- courtyard.py:        COURTYARD_CLEARANCE = 0.25mm
- orphaned_vias.py:    TOLERANCE = 0.01mm
- kink.py:             TOLERANCE = 1e-6mm
