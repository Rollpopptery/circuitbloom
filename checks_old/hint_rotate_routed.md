# HINT_ROTATE_ROUTED
# Location: utilities/checks/HINT_ROTATE_ROUTED.md
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline
#
# Invocation: read this file and follow steps 1–6 exactly.
# Context: post-routing optimisation only.
#          Board must have passed a clean verification pass before invoking.
#          Do not invoke during first-pass routing.
#
# Scope: 2-pad components only.


## CORE RULE

If the two track segments directly touching a 2-pad component exit from
OPPOSITE sides of the component, the component is a rotation candidate.

OPPOSITE sides means:
  - One track exits LEFT  and the other exits RIGHT  (horizontal opposition)
  - One track exits UP    and the other exits DOWN   (vertical opposition)

SAME side or ADJACENT sides = not a candidate. Do not flag.

This rule captures components that are sitting ACROSS a routing gap rather
than ALONG it. Rotating will bring the exits to the same or adjacent sides,
shortening the overall routed path.


## PROCEDURE

### STEP 1 — SELECT CANDIDATE COMPONENT

Component must satisfy:
- Exactly 2 pads
- Both pads connected by routed tracks in the current .dpcb
- Board has passed clean verification (no hard violations)


### STEP 2 — CALCULATE PAD POSITIONS

Using the verified KiCad 9.0.7 rotation convention:
```
r0:   (dx, dy) → ( dx,  dy)
r90:  (dx, dy) → ( dy, -dx)
r180: (dx, dy) → (-dx, -dy)
r270: (dx, dy) → (-dy,  dx)
```
abs_x = fp_x + rotated_dx
abs_y = fp_y + rotated_dy

Calculate and record the absolute position of both pads.


### STEP 3 — FIND EXITING TRACK SEGMENTS

For each pad, find the single TRK segment whose start or end point
coincides with that pad position (within 0.01mm tolerance).

For each segment, determine the exit direction FROM the pad:
  - If the other endpoint has greater x: exits RIGHT
  - If the other endpoint has lesser x:  exits LEFT
  - If the other endpoint has greater y: exits DOWN
  - If the other endpoint has lesser y:  exits UP


### STEP 4 — APPLY CORE RULE

Compare the two exit directions:

  RIGHT vs LEFT  → OPPOSITE → rotation candidate ✓
  UP vs DOWN     → OPPOSITE → rotation candidate ✓
  Any other combination → SAME or ADJACENT → not a candidate. Stop.

If not a candidate: print nothing. Do not proceed.


### STEP 5 — REPORT HINT

If candidate confirmed, report:

```
HINT [ROTATE_ROUTED] <component_ref>
  Pad1 (<x>,<y>) exits: <direction>
  Pad2 (<x>,<y>) exits: <direction>
  Exits are OPPOSITE — component is sitting across a routing gap.
  Action: rotate <component_ref> and re-route the two adjacent segments.
          Try 90°, 180°, 270° — pick rotation that brings exits to same
          or adjacent sides with shortest re-routed segment lengths.
          Verify clean pass after applying.
```

Do not automatically apply the rotation. This is a hint only.


### STEP 6 — APPLY IF INSTRUCTED

If explicitly instructed to apply:
1. Choose rotation (90°, 180°, 270°) that resolves the opposition and
   minimises total length of the two re-routed segments.
2. Update component rotation in .dpcb.
3. Remove the two baseline segments identified in Step 3.
4. Re-route both pads to their original far endpoints using minimum
   Manhattan distance. Use F.Cu unless crossing conflict, then B.Cu.
5. Validate: no pad conflicts, no crossings.
6. Run full verification pass (check_dpcb.py).
   If verification fails, revert all changes and report failure.


## NOTES

- This procedure only re-routes the two segments directly touching the
  component pads. All other tracks are untouched.
- The saving may appear in the re-routed segments themselves, or in
  adjacent bus segments that naturally shorten as a consequence.
  Either outcome is valid — the hint fires on topology, not measurement.
- Always run a full verification pass after applying any rotation.