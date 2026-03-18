# HINT_SHORT_EXIT
# Location: utilities/checks/HINT_SHORT_EXIT.md
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline
#
# Invocation: read this file and follow steps 1–3 exactly.
# Context: post-routing optimisation only.
#          Board must have passed a clean verification pass before invoking.
#          Do not invoke during first-pass routing.
#
# Scope: all pads on all components, both layers.


## CORE RULE

For every pad, find the first track segment leaving it.
If that segment is shorter than 5mm AND the next segment is
perpendicular to it (a direction change within 5mm of the pad),
flag it as a hint.

The hint means: this pad exits in a direction it immediately
abandons. The exit direction is probably wrong.

This is an observation only. The remedy may be a component
rotation, a re-route, or a placement adjustment — that is left
to human or AI judgement after the hint fires.


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


### STEP 2 — CHECK EACH PAD

For each pad P:

  2a. Find S1: the TRK segment whose start or end coincides with P
      (within 0.01mm tolerance).
      Record far endpoint M and layer L.
      
      S1_LENGTH = abs(M.x-P.x) + abs(M.y-P.y)

  2b. If S1_LENGTH >= 5mm: not a candidate. Skip this pad.

  2c. If S1_LENGTH < 5mm: find S2 on the same layer whose start
      or end coincides with M, that is NOT S1.

  2d. Check perpendicularity:
      S1 horizontal + S2 vertical → perpendicular ✓
      S1 vertical + S2 horizontal → perpendicular ✓
      S1 and S2 parallel → not a direction change. Skip.

  2e. If perpendicular: flag this pad.
      Record: PAD, S1_LENGTH, S1 exit direction, S2 direction, layer.


### STEP 3 — REPORT HINTS

For each flagged pad:

```
HINT [SHORT_EXIT] <component_ref>.<pad_id> [<layer>] net=<net>
  Pad     : (<P.x>,<P.y>)
  S1      : exits <direction> for <S1_LENGTH>mm to (<M.x>,<M.y>)
  S2      : changes to <direction> at (<M.x>,<M.y>)
  Observation: pad exits <S1 direction> but immediately turns <S2 direction>
               within <S1_LENGTH>mm. Exit direction may be suboptimal.
```

Do not suggest a specific fix. Do not automatically apply anything.
This is an observation only.


## NOTES

- The 5mm threshold is a tuning parameter. Increase it to catch
  longer unnecessary jogs. Decrease it to reduce noise.
- This hint may fire alongside HINT_ROTATE_ROUTED or kink.py on
  the same component. That is expected and desirable — overlapping
  rules provide independent confirmation.
- A short exit is not always wrong. Routing constraints may require
  it. Always apply human judgement before acting on this hint.
- Both F.Cu and B.Cu are checked independently per pad.