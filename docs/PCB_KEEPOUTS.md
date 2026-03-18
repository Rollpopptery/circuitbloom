# PCB KEEPOUTS
# Last updated: 2026-03-17
# Project: AI-Driven KiCad PCB Design Pipeline


## OVERVIEW

Keepouts are grid-level blocked zones that prevent the router from
placing tracks in specified areas. They are a strategic design tool —
used to protect IC bodies, guide routes into approach channels, and
enforce routing discipline around tight-pitch components.

Keepouts are hand-crafted design decisions. The designer decides
where to place them based on component geometry, pad pitch, and
routing intent.


## FILE FORMAT

Keepouts are stored in `.keepouts` files alongside the `.dpcb` file
(same basename, e.g. `test2_clean.keepouts` for `test2_clean.dpcb`).

The file is auto-loaded when the `.dpcb` is loaded by the viewer.

```
# Comments start with #
# Format: layer,gx,gy  (one grid cell per line)
# layer 0 = F.Cu, layer 1 = B.Cu
# gx, gy are grid coordinates at 0.1mm pitch
0,138,328
0,138,329
1,200,400
```

Grid coordinate conversion:
- mm to grid: gx = x_mm / 0.1 = x_mm * 10
- grid to mm: x_mm = gx * 0.1


## API COMMANDS

```
keepouts reload    — re-read from .keepouts file and apply to grid
keepouts clear     — remove all keepout cells from grid
keepouts save      — write current grid keepouts to file
keepouts status    — show keepout cell count per layer
```


## KEEPOUT PATTERNS

### Hollow Rectangle (IC Body)

A 2-cell-thick wall surrounding the IC body area. Blocks routes
from entering the component interior while using fewer cells than
a filled block. The hollow interior is unreachable because the
wall is continuous.

```
Wall thickness: 2 cells (0.2mm) — safe with any dilation margin
```

For a TSSOP-8 at r90, centred at grid (cx, cy):
```
dx range: ±12 (±1.2mm from centre)
dy range: ±28 (±2.8mm — extends to one cell short of pads at ±29)
Border:   cells where abs(dx) > 10 or abs(dy) > 26
```

### Pad Approach Channels

Vertical walls between adjacent pads that force routes to approach
each pad through its own dedicated channel. Prevents routes from
squeezing between tight-pitch pins.

For a TSSOP-8 at r90 (0.65mm pitch, pads at dx ≈ -10, -3, +3, +10):
```
Channel walls at dx = {-12, -6, 0, +6, +12}
  -12: outside wall (outer edge of pad at dx=-10)
   -6: between pads at dx=-10 and dx=-3
    0: between pads at dx=-3 and dx=+3
   +6: between pads at dx=+3 and dx=+10
  +12: outside wall (outer edge of pad at dx=+10)

Each wall extends from body edge outward:
  Top channels:    dy = -41 to -29 (above body, approaching top pads)
  Bottom channels: dy = +29 to +41 (below body, approaching bottom pads)
```

### Combined Pattern (TSSOP-8 r90)

The complete keepout for one TSSOP-8 at r90:
1. Hollow rectangle body: dx ±12, dy ±28, 2-cell wall
2. Five channel walls extending from body to dy ±41
3. Gap at dy ±29 for the pad row itself

Result: routes can only reach each pad through its own channel,
approaching from outside the IC body.


## DESIGN PRINCIPLES

### Keepouts are design intent
They express WHERE routes should go, not just where they shouldn't.
Channel walls guide routes into clean, predictable paths.

### One pattern per footprint type
Each footprint type (TSSOP-8 r90, SOIC-16, etc.) has a keepout
pattern defined as offsets from the footprint centre. The same
pattern is stamped at each instance position.

### Hollow is better than filled
Hollow rectangles use fewer cells, render faster, and make the
boundary visually clear. The router cannot enter the interior
because the wall is continuous.

### Wall thickness matters
Minimum 2 cells (0.2mm) ensures the wall blocks routes regardless
of the dilation margin setting. A 1-cell wall might be bypassed
with margin=0.

### Channel walls create routing discipline
Without channel walls, the router may thread between adjacent
0.65mm-pitch pads. The walls make this physically impossible,
forcing clean approach paths.


## COORDINATE REFERENCE

To compute keepout positions for a footprint at (fx, fy) with
rotation r:

1. Define the pattern as (dx, dy) offsets from centre at r0
2. Apply rotation to each offset (same convention as pad rotation)
3. Convert to grid: gx = (fx + rdx) * 10, gy = (fy + rdy) * 10
4. Write as layer,gx,gy

For TSSOP-8 at r90, the pattern is pre-rotated — the dx/dy offsets
in this document already account for the 90° rotation.
