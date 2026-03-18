# HINT_DIRECT_PAD_EXIT
# Location: utilities/checks/HINT_DIRECT_PAD_EXIT.md
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline
#
# Invocation: read this file and follow steps 1–5 exactly.
# Context: post-routing optimisation only.
#          Board must have passed a clean verification pass before invoking.
#          Do not invoke during first-pass routing.
#
# Scope: all pads on all components, both layers.


## CORE RULE

For every pad, find the first two track segments leaving it.
If they form an L-shape (one horizontal + one vertical, same layer),
check if a single direct segment from the pad to the far endpoint
of the L is shorter.

If shorter AND valid: flag it as a hint.
If invalid (pad conflict or crossing): discard silently.

A single segment is ALWAYS shorter than or equal to an L-shape
by Manhattan distance — so the only reason not to flag is
if the direct route is invalid.


## PROCEDURE

### STEP 1 — CALCULATE ALL PAD POSITIONS

For every footprint in the .dpcb, calculate absolute pad positions
using the verified KiCad 9.0.7 rotation convention:
```
r0:   (dx, dy) → ( dx,  dy)
r90:  (dx, dy) → ( dy, -dx)
r180: (dx, dy) → (-dx, -dy)
r270: (dx, dy) → (-dy,  dx)
```
abs_x = fp_x + rotated_dx
abs_y = fp_y + rotated_dy


### STEP 2 — FIND L-SHAPES

For each pad P:

  2a. Find segment S1: the TRK whose start or end coincides with P
      (within 0.01mm tolerance).
      Record the far endpoint of S1 — call it M (the midpoint of the L).

  2b. Find segment S2: the TRK on the SAME LAYER whose start or end
      coincides with M, that is NOT S1.
      Record the far endpoint of S2 — call it Q (the end of the L).

  2c. Check L-shape condition:
      S1 and S2 must be perpendicular (one horizontal, one vertical).
      If S1 and S2 are parallel (both horizontal or both vertical):
        this is not an L-shape. Skip this pad.

  2d. If L-shape confirmed, record:
      PAD=P, S1=(P→M), S2=(M→Q), LAYER, NET
      L_LENGTH = abs(M.x-P.x) + abs(M.y-P.y)
               + abs(Q.x-M.x) + abs(Q.y-M.y)
      DIRECT_LENGTH = abs(Q.x-P.x) + abs(Q.y-P.y)

      Note: for orthogonal routing DIRECT_LENGTH <= L_LENGTH always.
      SAVING = L_LENGTH - DIRECT_LENGTH


### STEP 3 — VALIDATE DIRECT ROUTE

For each L-shape found, check the proposed direct segment P→Q:

  3a. PAD CONFLICTS: the segment must not pass within
      (pad_radius + 0.2mm) = 1.0mm of any pad not on its own net.

  3b. CROSSINGS: the segment must not cross any existing track on
      the same layer from a different net.

  If either check fails: discard silently. Do not report.


### STEP 4 — REPORT HINTS

For each valid candidate:

```
HINT [DIRECT_PAD_EXIT] <component_ref>.<pad_id> [<layer>] net=<net>
  Current : (<P.x>,<P.y>) -> (<M.x>,<M.y>) -> (<Q.x>,<Q.y>)  L_LENGTH mm
  Direct  : (<P.x>,<P.y>) -> (<Q.x>,<Q.y>)                    DIRECT_LENGTH mm
  Saving  : <SAVING> mm
  Action  : replace S1+S2 with single segment P->Q.
            Verify clean pass after applying.
```

Do not automatically apply. This is a hint only.


### STEP 5 — APPLY IF INSTRUCTED

If explicitly instructed to apply:
1. Remove S1 and S2 from the .dpcb.
2. Insert new TRK:(P)->(Q):width:layer:net
   Use the same width as S1.
3. Run full verification pass (check_dpcb.py).
   If verification fails, revert and report failure.


## NOTES

- Only the first two segments from the pad are examined. Longer
  multi-segment paths are out of scope for this procedure.
- Both F.Cu and B.Cu are checked independently.
- A pad may have tracks on both layers — check each layer separately.
- SAVING is always >= 0 for orthogonal routing. The only reason to
  discard is failed validation, not length.