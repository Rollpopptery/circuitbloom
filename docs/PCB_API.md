# PCB API COMMAND REFERENCE
# Last updated: 2026-03-19
# Project: AI-Driven KiCad PCB Design Pipeline


## OVERVIEW

The viewer (dpcb_viewer.py) serves a TCP command API on port 9876.
The AI sends text commands and receives text responses. Every response
ends with `\n.\n` (dot on its own line).

### Quick command-line access

Two CLI wrappers — one for routing, one for placement:

**cmd.py** — routing work (route, unroute, via, etc.)
```bash
python3 utilities/cmd.py <command>
python3 utilities/cmd.py status
python3 utilities/cmd.py route +5V 9.85,4.05 9.5,11.75 auto margin=3
```

After state-changing commands (route, unroute, via, move, load, save),
automatically runs diagnostics: viacheck, check_crowding, check_crowding_pads,
get_transitions. Shows diffs against previous state (NEW/FIXED).

**cmd_component.py** — placement work (move, placement analysis)
```bash
python3 utilities/cmd_component.py move R1 8.5,12.5
python3 utilities/cmd_component.py move U2 19.0,10.0 r90
python3 utilities/cmd_component.py check_crowding_pads
python3 utilities/cmd_component.py check_ratsnest
python3 utilities/cmd_component.py force U2
python3 utilities/cmd_component.py repulsion U2
python3 utilities/cmd_component.py component_repulsion
python3 utilities/cmd_component.py pads +5V
python3 utilities/cmd_component.py status
```

After move/load/save, automatically runs placement diagnostics:
- PAD CROWDING (check_crowding_pads 1.5) — with diff
- RATSNEST BLOCKED (check_ratsnest 2.0) — with diff
- FORCE — all components, attraction toward connections
- REPULSION — all components, push from foreign ratsnest lines
- COMPONENT REPULSION — all components, physical spacing pressure

Uses separate flash state file so it does not interfere with cmd.py.

Configurable via environment variables:
- `DPCB_HOST` — viewer host (default: 172.17.0.1, Docker gateway)
- `DPCB_PORT` — viewer port (default: 9876)

### Programmatic connection from Docker container

```python
import socket
HOST = '172.17.0.1'  # Docker gateway
PORT = 9876
s = socket.create_connection((HOST, PORT), timeout=5)
```


## BOARD MANAGEMENT

### load

```
load <filename>
```

Load a .dpcb file. Uses HOST path (not container path). Rebuilds the
routing grid. Auto-loads `.keepouts` file if present (same basename).

```
load /home/ric/projects/pcb_design/project/board.dpcb
```

### save

```
save <filename>
```

Write current board state (including routes) to a .dpcb file.

### status

```
status
```

Board dimensions, component/net/track counts, grid utilisation.

```
OK: 29.84x20.32mm fps=19 nets=22 trks=0 vias=0 grid=298x203 fcu=0.0% bcu=0.0%
```

### nets

```
nets
```

List all nets with pad connections and routed status.

```
OK: 22 nets
  +5V: C2.1,D3.1,J3.1,R1.2,... [unrouted]
  GND: C1.2,U1.2,... [18trk]
```

### help

```
help
```

List available commands.

### quit

```
quit
```


## COMPONENT COMMANDS

### move

```
move <ref> <x,y> [r<rot>]
```

Move a component to a new position. Optionally change rotation (0, 90,
180, 270). Updates the grid immediately.

```
OK: moved C1 from (15.975,27.0):r90 to (19.0,27.0):r90
```

### pads

```
pads <net>
```

Show absolute pad positions for a net. Computed from footprint position +
rotated pad offset — no manual calculation needed.

```
OK: GND pads:
  C1.2 @ (19.0,27.95)
  R1.2 @ (15.975,42.0875)
  U3.1 @ (15.0,37.8625)
```


## ROUTING COMMANDS

### route

```
route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto] [margin=N] [use8]
```

Route a track between two points. Layer modes: F.Cu, B.Cu, or auto
(router chooses, may insert vias). Margin sets dilation in grid cells
(0.1mm each). `use8` enables 8-direction (diagonal) routing.

Always use `margin=3` on every route command.

```
OK: Routed: 14.3mm, 0 vias, 3 segs
  path: (21.5,13.9) -> (17.5,13.9) -> (17.5,50.0) -> (21.7,57.1)
FAIL: No path found (245000 iters)
```

### route_tap

```
route_tap <net> <x,y> [F.Cu|B.Cu|auto] [margin=N]
```

Route from a pad to the nearest existing trace on the same net. Only
needs one coordinate — the router finds the closest same-net cell and
connects to it automatically. Returns the tap point where it connected.

Use for multi-pad nets: route the main chain first with `route`, then
tap remaining pads to the trunk with `route_tap`. No coordinate guessing
needed for the junction point.

```
OK: Tapped: 3.1mm, 0 vias, 1 segs  tapped at (8.6,10.9)
  path: (5.5,10.9) -> (8.6,10.9)
FAIL: No path to net (50000 iters)
```

### unroute

```
unroute <net> [path_index]
```

Remove all routed tracks for a net (or a specific path segment).

### unroute_seg

```
unroute_seg <net> <x1,y1> <x2,y2> [tolerance]
```

Remove a specific track segment matching net and endpoints. Default
tolerance 0.15mm. Use for surgical segment replacement within complex
nets.

### via

```
via <net> <x,y>
```

Place a via at a specific position. Pre-seed vias before routing to
control where layer transitions happen. Always run `viacheck` after.

### waypoints

```
waypoints <net>
```

Show waypoints for routed paths of a net.

### pushout

```
pushout <net> [amount]
```

Push routed traces outward from obstacles.

### clearkeepouts

```
clearkeepouts <net>
```

Clear keepout cells from routed paths of a net.


## KEEPOUT COMMANDS

### keepouts

```
keepouts reload|clear|save|status
```

Manage grid keepout zones.

- `reload` — re-read from .keepouts file
- `clear` — remove all keepout cells
- `save` — write current keepouts to file
- `status` — show keepout cell count per layer


## INSPECTION COMMANDS

### probe

```
probe <x,y>
```

Show grid cell state at a position (mm). Reports net occupancy on both
layers, pad keepout status, and pad layer assignment.

```
OK: (15.0,35.0) grid=(150,350) F.Cu=0 B.Cu=0 pad_keepout=False pad_layer=none
```

### get_vias

```
get_vias
```

List all vias on the board with position and net.

### get_transitions

```
get_transitions [tolerance]
```

Find all layer transitions in routes. Each transition marked VIA (via
exists) or MISSING (no via). Default tolerance 0.15mm.


## QUALITY CHECK COMMANDS

### viacheck

```
viacheck [threshold_mm]
```

Check every via for proximity to pads. Default threshold 2.0mm.
FAIL means the via is too close and should be relocated.

```
OK: 7 via(s) checked, threshold=2.0mm
  OK   (36.3,16.0) /clk  nearest=U1.9 dist=9.84mm
  FAIL (37.3,31.4) /clk  nearest=U5.2 dist=0.74mm
```

### check_crowding

```
check_crowding [threshold_mm]
```

For every component, measures clearance to nearest foreign-net track
segment. Default threshold 1.0mm. Only useful after routing — reports
0 crowded if no tracks exist.

### check_crowding_pads

```
check_crowding_pads [threshold_mm]
```

For every component, finds the nearest pad on a different component
(regardless of net — physical overlap is never OK). Default threshold
1.5mm. Sorted closest-first.

Use during placement to detect overlapping or too-close pads before
routing.

```
OK: 19 component(s) checked, 2 crowded  (threshold=1.5mm)
  CROWDED  D5   (17.0,17.0)  nearest_pad=0.8mm  to=R6.2  net=PB3
  ok       R1   (8.5,13.5)   nearest_pad=1.63mm  to=D1.2  net=GNDREF
```

### check_ratsnest

```
check_ratsnest [threshold_mm]
```

Builds nearest-neighbour ratsnest chains for all nets, checks if any
foreign-net pads sit close to those lines. Reports ALL blockers per
line, sorted by distance. Default threshold 2.0mm.

Use during placement to find components sitting in other nets' routing
corridors.

```
OK: 45 ratsnest blockage(s)  (threshold=2.0mm)
  BLOCKED  PA6  J3.8->U2.7  by D4.2 (Net-(D4-Pad2)) dist=0.11mm
  BLOCKED  PA7  J3.7->U2.6  by D5.2 (Net-(D5-Pad2)) dist=0.15mm
```


## PLACEMENT ANALYSIS COMMANDS

### force

```
force [ref]
```

Show attraction force vector for a component (or all if no ref). Each
connected pad on another component pulls with a unit vector. Returns
sum force, magnitude, target point, and per-net breakdown.

Force is an INDICATOR only — shows direction of connection pull, not a
score. High magnitude does not mean bad placement. Use it to decide
WHICH DIRECTION to move when crowding or ratsnest blockages trigger a
move.

```
OK: C2 (9.5,10.5)  force=(1.19,1.8)  mag=2.16mm  toward=(10.69,12.3)
  +5V: (2.22,2.28) from 8 pad(s)
  GNDREF: (-1.03,-0.48) from 11 pad(s)
```

### repulsion

```
repulsion [ref] [threshold_mm]
```

Show repulsion vector from nearby foreign ratsnest lines. Each foreign
ratsnest line within threshold pushes the component away, weighted by
inverse distance (closer = stronger). Default threshold 3.0mm.

Complements force:
- **force** = pull toward own connections
- **repulsion** = push away from other nets' routing corridors

```
OK: D3 (4.5,11.0)  repulsion=(0.5,-1.2)  mag=1.3mm  push_toward=(5.0,9.8)  from 5 line(s)
  Net-(D1-Pad1): (0.3,-0.8) from 2 line(s)
  Net-(D2-Pad1): (0.2,-0.4) from 3 line(s)
```


### component_repulsion

```
component_repulsion [ref]
```

Show physical repulsion vector from ALL other components, regardless of net.
This is Coulomb-like pairwise repulsion based on component size (pad bounding
box radius) and inverse distance squared. Closer and larger components push
harder.

Use this to "feel" the physical spacing of the layout without seeing the
board. High magnitude means the component is crowded by neighbours. The
vector direction shows which way to move for more breathing room.

Complements force and repulsion:
- **force** = pull toward own connections (attraction)
- **repulsion** = push away from other nets' routing corridors
- **component_repulsion** = push away from all nearby components (physical spacing)

Without ref: returns all components sorted by magnitude (most crowded first).
With ref: returns single component with top 5 nearest contributors.

```
OK: 19 component(s)
  R6   (2.0,7.0)  r=1.1mm  push=(-1.16,0.63)  mag=1.33mm
  D3   (3.0,6.0)  r=1.4mm  push=(0.69,-0.83)  mag=1.08mm
  J2   (7.4,0.8)  r=8.89mm  push=(0.26,-0.92)  mag=0.96mm
  ...
```

```
OK: R6 (2.0,7.0)  radius=1.1mm  repulsion=(-1.16,0.63)  mag=1.33mm  push_toward=(0.84,7.63)  from 18 component(s)
  D3: (-0.8839,0.8839) dist=1.41mm
  J1: (-0.0588,-0.3719) dist=3.2mm
  U1: (-0.0148,0.1334) dist=4.53mm
  ...
```

The `r=` field is the component radius — half the diagonal of the pad
bounding box. Larger components (ICs, connectors) have larger radii and
push harder. The radius also indicates how much board space the component
occupies.

Physics model: for each pair (A, B), the force on A from B is:
  direction = unit vector from B to A (pushes A away)
  magnitude = (radius_A + radius_B) / distance²


## LOGGING

### log_note

```
log_note <text>
```

Write a free-form note to `<board>.design.log`. Records routing
reasoning, strategy decisions, placement rationale. The log also
auto-captures route, unroute, via, and move actions.
