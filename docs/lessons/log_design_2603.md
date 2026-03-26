# Design Log — 2026-03-26

## Route Server Refactor

Modularised `route_server.py` into:
- `viewer.html` — HTML/CSS/JS canvas viewer
- `route_convert.py` — path conversion utilities
- `route_state.py` — shared state, bloom load/save
- `route_handlers.py` — HTTP handlers (browser + agent)
- `route_server.py` — thin entry point

## Interactive Viewer

- Right-click drag to pan, left-click drag to move components
- Mouse wheel zoom, hover net highlighting
- Component outlines drawn as white rectangles
- Zoom/pan preserved across auto-reloads (sessionStorage)
- R key rotates grabbed component 90 degrees clockwise

## Topology → Grid Placement

Explored several placement models:
1. **Nested row/column tree** with spacers — complex, orphan spacer problems
2. **Flat row-per-component** with spacers — merge/split edge cases
3. **Integer grid placement** — simple, clean, won

Final model: `placement` dict in bloom file:
```json
"placement": {
  "XJ1": {"col": 2, "row": 2, "w": 7, "h": 3},
  ...
}
```
- `col`/`row` in grid cells (1mm each), integer positions
- Resolver: `x_mm = col * SCALE`, trivial
- Drag adjusts col/row by integer deltas
- No spacers, no orphan state

Removed `layout_tree` and component `position` fields from bloom.

## API Endpoints Added

- `GET /placement` — all component positions
- `GET /placement/<ref>` — single component
- `POST {action: "move", ref, dw, dh}` — relative move
- `POST {action: "place", ref, col, row}` — absolute placement
- `POST {action: "rotate", ref}` — 90 degree clockwise rotation

## Routing

Started routing nets via API. Completed:
- LED1_K, LED1_A, ISENSE, LORA_DIO0, LORA_RST
- SPI bus: MOSI, MISO, NSS, SCK (some needed margin=1 + vias)
- VIN_PRE chain, VBAT_IN, LOAD_NEG chain
- VCC_3V3 chain (6 pads), GND net (13 pads — 2 may need verification)
- 275 tracks, 9 vias total

## Issue: Clearance Violations

Traces routed too close to pads and other traces. Some routes used margin=1 to
get through congested areas, resulting in clearance violations. Need to re-route
with minimum clearance of 0.3mm (matching board rules `clearance: 0.2`, track
width 0.25). Should use margin=3 on all routes going forward and rework tight
sections that used margin=1.

## Design Rule: No traces between U4 pins

Do not route traces between U4 (RA01) pins. Pads are too close together on this
module — traces between pins will violate clearance. Route all U4 connections
with fan-out stubs away from the IC body first.

SPI_NSS (U4.15) and GND (U4.16) traces currently go between pins — need re-route.

Narrow pass at (26, 23) — SPI_NSS and SPI_SCK traces need adjusting to make
room for SPI_MISO and SPI_MOSI to pass through. Route MOSI and MISO first
through this corridor, then NSS and SCK around them.

SPI_NSS fix: fan-out stub from U4.15 going left then down to waypoint (27,22)
below U4 extent, then use8 45-degree trace up to XU1.2. Very clean result —
trace avoids all U4 pins with good clearance. This waypoint-below-IC pattern
works well for fan-out routing.

## Fix: U4 (RA01) pad classification

U4 was incorrectly classified as through-hole. RA01 is a castellated SMD module.
Added 'RA01' to SMD_PACKAGES in bloom_grid.py. This affects pad rendering size
in viewer and grid marking (SMD = F.Cu only, TH = both layers).

## Routing Strategy: Fan-out from IC pads

Don't route traces parallel along IC pad edges — they block other pads from
access. Instead:
1. Short horizontal stub outward from each pad (perpendicular to IC edge)
2. Use 8-way router (use8=true) for 45-degree traces after the stub
3. This creates a fan pattern that keeps all pads accessible

Applies especially to U4 (RA01) and XU1 (SOIC-20) where many pads are in a line.

## Clearance Target: 0.3mm minimum

All traces must have at least 0.3mm clearance to nearest foreign obstacle.
Use `GET /clearance` to audit, unroute and redo any net below 0.3mm.

## Feedback: Don't blame placement

Route has plenty of room and can happen easily yet Claude complained to user that
placement needs more breathing room. Don't suggest placement changes when routes
fail — try harder, try different order, try vias, try B.Cu. The board has space.

## Bug: Trace routed through pad (FIXED)

Router traced through XU3.5 pad. Root cause: after rotate/move via viewer,
`_rebuild_from_placement` updates pad positions in state but does NOT rebuild
the routing grid. Grid still has pads at old pre-rotation positions.

Fix: save and reload after rotate/move to rebuild grid with correct pad
positions before routing. Long-term fix: `_rebuild_from_placement` should
also rebuild the routing grid.

## GND Strategy

GND will use B.Cu as a ground plane. Route short traces from GND pads to vias,
then connect via B.Cu. Don't route long GND traces on F.Cu — drop to B.Cu early.

Systematic approach: route one pad at a time, short ~5mm F.Cu stub to a via,
then extend on B.Cu. Starting with XU3.4 first.

To force a via: use place_via command. Workflow:
1. route F.Cu stub from pad
2. place_via at stub endpoint
3. route B.Cu between vias
4. place_via at other end
5. route F.Cu stub to next pad

First GND segment (XU3.4 → RSENSE.2) looks good. Continue rest of GND net.

## Density Map API

Added `GET /density` — returns board divided into 10x10mm sectors with F.Cu and
B.Cu occupancy % and pad count per sector. Gives Claude spatial awareness of
congestion before routing. Call once before planning routes.

## VCC_3V3: Use clear line-of-sight routes

Don't always rely on A* — look for straight-line clear paths between pads.
E.g. U4.3 to XU2.2 has a clear direct path. An obstacle check API (probe
straight line between two points) would help Claude assess routing options
before attempting routes.

## LED1_A routing attempt

Use stub+via+B.Cu pattern for LED1_A (XU1.7 → LED1.1). F.Cu corridors blocked
by SPI routes, so drop to B.Cu early.

## Fan-out stub graduation

When multiple stubs fan out from an IC and the auto-router will route downward
(or any consistent direction), graduate stub lengths so the top stub is longest
and each one below is shorter. This spreads the stub endpoints so downward
traces don't stack on top of each other.

Example for U4 right-side pads routing down-left:
- U4.15 (top, y=4):  stub 5mm left → endpoint at x=37.5
- U4.14 (y=6):       stub 4mm left → endpoint at x=38.5
- U4.13 (y=8):       stub 3mm left → endpoint at x=39.5
- U4.12 (y=10):      stub 2mm left → endpoint at x=40.5

This anticipates the router's direction and pre-spreads the corridors.

Result: graduated fan-out on U4 SPI pads produced very clean traces. MOSI 0.40mm,
MISO 0.41mm, SCK 0.50mm clearance. Only NSS at 0.22mm needs attention. The
approach works — use it for all multi-pin IC fan-outs going forward.

SPI_NSS waypoint at (28,16) still 0.20mm clearance to U4 pad at x=26.5.
Waypoint (27,18) would be better — further from U4 left-side pads which
end at y=16. Waypoints for U4 traces should be below y=16 (bottom of U4
left pins) and left of x=26.5 (U4 left edge).

## SPI bottom pad routing strategy

XU1.16/17/18 (MOSI/MISO/SCK) are on XU1 bottom edge. Stubs must go UP into
the IC body area, not down. Down-stubs cause dog-legs (backtracking) and block
adjacent pads. SCK routed with stub up + via + B.Cu diagonal. MISO stub up +
F.Cu diagonal. MOSI stub down (only option after others placed).

Small jitter segments appear where traces squeeze past nearby vias — acceptable
but could be cleaned up with post-route smoothing.

## Polish: Replace right-angles with 45-degree sections

After routing is complete, audit traces for right-angle bends. Replace with
45-degree chamfers — shortens overall trace length and improves signal integrity.
Use `use8: true` when re-routing segments that have right-angle corners.

Polish pass: audit all nets for 90-degree bends, replace with 45-degree chamfers.

Chamfering means replacing the right-angle corner with a short diagonal segment.
Example: LORA_RST stub goes horizontal to (23.5, 8) then vertical down. Instead,
route horizontal to (24, 8) then diagonal to (23, 10) — this chamfers the corner.
The stub+diagonal replaces the stub+vertical with a smoother transition.

Don't just re-route whole nets with use8 — manually insert chamfer segments at
the specific right-angle corners.

Chamfer technique proven on LORA_RST, LORA_DIO0, SPI_NSS, SPI_MOSI, LED1_A,
VIN_PRE. All look good after chamfering. ISENSE B.Cu path still has jitter from navigating obstacles — acceptable.

## T-junction routing with nearest_track

Added `GET /nearest_track?net=X&x=N&y=N` — finds closest point on any existing
trace of the net (not just endpoints). Use this for T-junctions: find nearest
point, route pad to that point. Cleaner than route_tap which only finds endpoints.

Tested on VCC_3V3 XU3.5 — `nearest_track` found point (12.1, 26.8) on the
C3.1→C1 trunk diagonal. Routed XU3.5 to that point: 1 segment, clean 45-degree
T-junction into the trunk. Very nice result — much better than separate branch
to C3.1. Use this pattern for all branch connections going forward.

## C2.1 unconnected

C2.1 (VIN_PRE) had disconnected stump from old tap. Full unroute and re-route
of VIN_PRE: F1.1 stub → chamfer → XU2.1, then C2.1 direct to XU2.1 with use8.
No residual stump. Lesson: when connecting a pad with a stump, unroute the
whole net and rebuild clean rather than adding to existing stump.

C2.1 re-routed to T-junction into the F1→XU2.1 chamfer segment at (9.0, 14.2)
instead of meeting at XU2.1 pad with a sharp angle. Much shorter and cleaner —
avoids two traces arriving at same pad from similar direction. Use nearest_track
to find T-junction points, then route branch to trunk rather than branch to pad.

## Routing Complete

All multi-pad nets routed. 250 tracks, 19 vias, 0 orphan vias. All clearances
at 0.30mm or better (target: 0.30mm). GND uses B.Cu plane with stub+via pattern.
SPI bus uses graduated fan-out from U4. Chamfered right-angle corners. T-junctions
via nearest_track for clean branch connections. 10 XU1 GPIO pins unconnected
(single-pad, no destination — normal for dev board).

Board: 55x35mm, 2 layers.

## ISENSE trace improvement

ISENSE 33.2mm vs 15.3mm straight line — 2x longer due to northward B.Cu detour
around GND/VCC traces. B.Cu waypoint at (14,23) reduced trace from 33.2mm to
28.3mm. Looks better — still has B.Cu jitter navigating GND traces but the
northward detour eliminated.

## Completing GND net

Connecting all remaining GND pads with stub+via+B.Cu pattern. U4 pads fan out
away from IC body (left for left pads, right for right pads). All GND B.Cu traces connect through via network on bottom layer.

## XU3.5 and XU3.3 stubs need connecting

XU3.5 (VCC_3V3) and XU3.3 (LOAD_NEG) have stubs from pads but no trace
connecting them to their nets. Route these — use small stubs to get router
started if necessary.

Chamfer technique: stub to corner, then 45-degree segment
to (22,10) using use8, then continue. Key: dx must equal dy for true 45-degree
(e.g. 2mm horizontal + 2mm vertical = one diagonal). Non-equal dx/dy produces
multiple segments, not a clean diagonal. Always use use8 for the chamfer segment.

## SPI_SCK stub direction fix

SPI_SCK stub from XU1.18 goes down toward other bottom pads, blocking MOSI/MISO
from exiting upward. Horizontal jog at y=25 runs parallel to pad row. Fix: stub
goes up into XU1 body area, then diagonal to U4. Keeps bottom pad corridor free.

## ISENSE re-route

ISENSE trace (XU3.1 to XU1.4) runs through XU1 pin corridor, blocking other
pads and causing clearance violations. Re-route to stay clear of XU1 pins —
use stub from XU1.4 going up/right away from IC, then via + B.Cu to XU3 area.

## Routing: Net by net around XU3

Testing routes to each XU3 pad individually after rotation fix, to verify
pad avoidance works correctly on all pins.

## User Fix: Rotated XU3

User rotated XU3 to improve routing access to its pads. Re-route needed for
ISENSE, LOAD_NEG, VCC_3V3, GND after rotation.

## Routing Strategy

Route all nets with margin=3 first. Deal with failed routes afterward — don't
drop margin to force routes through. Failed routes indicate placement needs work.

## Note: CLI Usage

Use single-line bash commands (no newlines) so user doesn't need to click 'yes'
for each command. Chain with `&&` on one line.
