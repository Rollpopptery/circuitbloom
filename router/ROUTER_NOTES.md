# Route Server — API Reference

## Overview

The route server is a bridge between the AI agent (Claude) and KiCad. KiCad runs on
the host with its IPC API exposed. The agent talks to the route server via MCP tools,
and the route server talks to KiCad via the kipy (kicad-python) IPC API.

**Loading a PCB file**: Use `open_board` which kills any running KiCad, launches
`pcbnew` with the file, waits for the IPC API, then captures the board state.
Path must be on the host filesystem (e.g. `/home/ric/projects/pcb_design/...`).
The `kicad-cli` binary (for DRC etc.) is accessed on the host.

```
Agent ──MCP tools──► Route Server ──kipy IPC──► KiCad (host)
```

## Architecture

- `route_server.py` — Entry point, arg parsing, file watcher
- `route_state.py` — Shared state, placement, routing helpers
- `route_handlers.py` — HTTP handlers (browser port 8083, agent port 8084)
- `route_convert.py` — Path-to-segment conversion, net colors
- `viewer.html` — Interactive HTML/Canvas viewer
- `bloom_grid.py` — Grid building, pad positions
- `tree_to_xy.py` — Placement resolver (grid-cell to mm)
- `dpcb_router.py` — Core A* router, grid management
- `dpcb_router8.py` — 8-direction A* router (45-degree traces)
- `grab_layer.py` — KiCad IPC capture (pads, tracks, vias, copper grids)
- `kicad_route.py` — Push routes to KiCad (tracks, vias, delete)
- `rebuild_routes.py` — Reconstruct logical routes from raw track segments
- `component_info.py` — Component/pad lookup from KiCad board (value, footprint, pins)
- `pad_info.py` — Lazy-loaded pad/component query via KiCad IPC (used by endpoints)
- `route_examples.py` — Search ChromaDB route examples database
- `route_planner.py` — Pre-routing analysis: corridors, conflicts, constraint scoring, layer assignment
- `board_render.py` — Render board state to PNG for visual feedback

## Grid

- Pitch: 0.1mm per cell
- Layers: N copper layers, auto-detected from KiCad board (e.g. 2-layer or 4-layer)
- Layer indices assigned in stack order: F.Cu=0, In1.Cu=1, In2.Cu=2, ..., B.Cu=last
- Layer names come from KiCad and can be custom (e.g. C1F, C2, C3, C4B)
- `occupy[layer][y, x]`: 0=empty, >0=net_id, -1=obstacle
- `pad_layers[(gx, gy)]`: layer index for SMD pads, None=all layers (through-hole)
- `pad_keepout`: set of (gx, gy) where vias are blocked
- Vias are through-hole only (connect all layers). Blind/buried vias not yet supported.

### Pad rasterisation (single pass, per-pad ownership)

Pads are written into the grid from the rasterised KiCad pad polygons. The
ownership is preserved at the moment of rasterisation, so each cell belongs
to exactly one pad:

1. `grab_layer.get_copper_grids` rasterises every pad polygon into two
   parallel per-layer grids:
   - `copper_grids[layer]` — the old 0/1/2/3 bitmap (pad/track/via), used
     for the visual heatmap.
   - `pad_owner_grids[layer]` — an int32 array where each cell stores the
     1-based index of the pad whose polygon rasterised it, or 0.
2. `route_state.build_router_grid_from_capture` walks `pad_owner_grids`,
   translates each non-zero cell's pad index → net name → nid, and writes
   `grid.occupy[layer][y, x] = nid`. It also records the same cells in
   `grid.pad_cells[layer][nid]` as the authoritative pad map used by the
   connectivity audit in `get_transitions`.

No capture-time clearance dilation. The design-rule clearance is enforced
**at query time** by `build_blocked_grid` (A\*), `get_clearance` (audit),
and `handle_add_track` (pre-flight). They each scan a neighbourhood of
`clearance_cells` around the track footprint when deciding legality.

This replaces an older "scan-window + Pass-2 dilation" approach that tried
to reconstruct pad ownership from the union copper bitmap after the fact;
at pin pitches below ~2mm its scan windows overlapped between neighbouring
pads and mis-attributed copper pixels, which wedged every tight-pitch pin
exit.

### Clearance is authoritative

**Clearance correctness is defined by the grid, not by pad-centre distance
arithmetic.** Pad cells are the real KiCad copper polygon pixels (via
`pad_owner_grids`), not circular approximations. The router, the clearance
audit, and the add_track pre-flight all consult the same `grid.occupy` map.

`mark_line` is write-if-empty-or-same-net, so a manually inserted bad
track does **not** overwrite pad cells underneath — collision evidence
survives in the grid for later audit.

## Placement Format

Components positioned by integer grid-cell coordinates:

```json
"placement": {
  "XU1": {"col": 20, "row": 16, "w": 13, "h": 9},
  "U4":  {"col": 35, "row": 14, "w": 17, "h": 16}
}
```

Resolver: `x_mm = col * SCALE`, `centre = position + size/2`. SCALE = 1.0mm.

## Browser (port 8083)

Interactive viewer (view-only mode — editing done in KiCad):
- Right-click drag to pan
- Mouse wheel zoom (preserved across reloads via sessionStorage)
- Hover shows pad info + cursor coordinates (x, y) in mm
- Dynamic layer toggle buttons (auto-detected from board, sorted alphabetically)
- Layer colors assigned by stack position: front=red, inner=yellow/green, back=blue
- Pads overlay shows rasterized pad and via copper shapes (tracks not rasterized — drawn from vector data)
- Auto-reloads on state change (polls /version every 500ms)

## Agent API (port 8084)

### GET Endpoints

| Path | Returns |
|------|---------|
| `/` | Full state (pads, tracks, vias, board, nets, components) |
| `/version` | `{v: N}` — state version counter |
| `/status` | Grid stats, track/via/pad/net counts |
| `/nets` | `{net_name: color, ...}` |
| `/pads` | All pads `[{ref, pin, name, net, x, y, smd}, ...]` |
| `/pads/<net>` | Pads for a specific net |
| `/placement` | All component placements `{ref: {col, row, w, h}, ...}` |
| `/placement/<ref>` | Single component placement |
| `/density` | 10mm sector density map — F.Cu/B.Cu occupancy % per sector |
| `/clearance` | Design-rule clearance audit. Walks each track's footprint cells, scans a `clearance_cells` neighbourhood for foreign copper, and reports the worst (smallest) true edge-to-edge distance per (foreign-net, near-pad) pair. `distance_mm = 0` means the track physically overlaps the foreign copper. Anything below the design rule is a violation. |
| `/orphan_vias` | Vias not connected to any trace endpoint |
| `/get_vias` | All vias |
| `/get_transitions` | Layer transition points, flags missing vias |
| `/nearest_track?net=X&x=N&y=N` | Closest point on any trace of net to (x,y) — for T-junctions |
| `/footprints` | All footprint mappings `{package: {kicad_mod, pads}, ...}` |
| `/find_via_spot?net=X&x=N&y=N&margin=3` | BFS from pad to find nearest reachable via spot (clear path guaranteed) |
| `/drc` | Run KiCad DRC on the board file (requires `--board` flag) |
| `/save` | Save tracks/vias/placement to server state |
| `/reload` | Reload server state and rebuild grid |
| `/capture_kicad` | Capture board state from running KiCad (pads, tracks, vias, copper heatmap) |
| `/capture_kicad?socket=<path>` | Capture with explicit socket path (e.g. `ipc:///tmp/kicad/api-41011.sock`) |
| `/push_kicad` | Push all tracks and vias from server state to KiCad |
| `/render?x1=&y1=&x2=&y2=&w=600` | Render board viewport to PNG image |
| `/pad_info?ref=U1&pin=3` | Pad + component info (net, value, footprint, position) |
| `/component_info?ref=U1` | Full component info (value, footprint, pins, position, mounting) |
| `/route_examples?q=text&n=5` | Semantic search over route examples database |

### POST Actions

All POST to `/` with JSON body `{action: "...", ...}`.

#### Routing

```json
{"action": "route", "net": "VCC_3V3", "from": [x1,y1], "to": [x2,y2],
 "margin": 3, "layer": "auto", "use8": true, "width": 0.25}
```
- `margin`: clearance in grid cells (1=0.1mm, 2=0.2mm, 3=0.3mm, etc). Default 3, reduce for tight areas.
- `layer`: `"F.Cu"`, `"B.Cu"`, or `"auto"` (allows vias)
- `use8`: 8-direction routing for 45-degree traces
- Endpoints snap to nearest pad on the net

```json
{"action": "route_tap", "net": "GND", "from": [x,y], "margin": 3}
```
Route from a pad to the nearest existing trace on its net.

```json
{"action": "unroute", "net": "GND"}
```
Remove all tracks and vias for a net. Returns `impact` showing clearance improvements.

**Route Response — Design Impact**

Both `route` and `route_tap` return an `impact` object showing how the routing
affected the overall design state:

```json
{
  "ok": true,
  "message": "routed 15.2mm via B.Cu",
  "length": 15.2,
  "vias": 2,
  "segments": 5,
  "impact": {
    "overall_min": {"before": 0.4, "after": 0.25},
    "new_route": {
      "net": "SPI_MOSI",
      "clearance": 0.25,
      "at": [20.3, 15.1],
      "layer": "F.Cu"
    },
    "degraded": [
      {"net": "GND", "before": 0.5, "after": 0.25, "delta": -0.25, "at": [20.5, 15.0], "layer": "F.Cu"}
    ],
    "improved": [],
    "tracks_added": 5,
    "vias_added": 2
  }
}
```

- `overall_min`: Global minimum clearance before/after this route
- `new_route`: Clearance info for the just-routed net
- `degraded`: Other nets whose clearance got worse (ripple effects)
- `improved`: Other nets whose clearance improved (rare)
- `tracks_added/vias_added`: What was added by this route action

#### Placement

```json
{"action": "move", "ref": "XU1", "dw": 2, "dh": -1}
```
Relative move — adjusts col/row by delta grid cells.

```json
{"action": "place", "ref": "XU1", "col": 10, "row": 5}
```
Absolute placement — sets col/row directly.

```json
{"action": "rotate", "ref": "XU1"}
```
Rotate 90 degrees clockwise. Swaps w/h in placement, updates component rotation.

#### Track Segments

```json
{"action": "add_track", "net": "GND", "x1": 5.0, "y1": 14.0,
 "x2": 5.0, "y2": 22.0, "layer": "B.Cu", "width": 0.25}
```
Add a single track segment manually. **This is the preferred way to place
traces** — see the Routing Strategy section and `CLAUDE.md` in this
directory.

**Pre-flight clearance check.** Before accepting the segment, `add_track`
walks the cells the track would cover (Bresenham centreline dilated by
the trace half-width) and, for each of those cells, scans a neighbourhood
of `grid.clearance` cells in `occupy[layer]` for foreign copper. If any
foreign net cell falls inside that (half_w + clearance) disc, the request
is refused. Same check is used by `get_clearance` for post-hoc auditing.

```json
{
  "ok": false,
  "error": "clearance_violation",
  "message": "refused: track on net 'SPI_NSS' would collide with LORA_DIO0 at [27.3, 13.3] (F.Cu)",
  "violation": {"at": [27.3, 13.3], "layer": "F.Cu", "near_net": "LORA_DIO0"},
  "track": { ... }
}
```

If you see this, the segment is wrong — adjust the sketch, do not fight
the checker. The same check backs `GET /clearance` for auditing routes
that were already placed (e.g. by the A\* router or imported from KiCad).

```json
{"action": "delete_tracks", "net": "GND",
 "x_min": 2, "y_min": 12, "x_max": 12, "y_max": 24}
```
Delete segments where both endpoints fall within the bounding box.
Net filter is optional — omit to match all nets.

```json
{"action": "delete_via", "net": "GND",
 "x_min": 2, "y_min": 12, "x_max": 12, "y_max": 24}
```
Delete vias within the bounding box. Net filter is optional.

#### Via

```json
{"action": "place_via", "net": "GND", "x": 10.3, "y": 34.0}
```
Place a via at a specific point. Use `/orphan_vias` to check for unconnected vias.

#### Footprints

```json
{"action": "set_footprint", "package": "0805", "kicad_mod": "LED_SMD.pretty/LED_0805_2012Metric.kicad_mod"}
```
Set or modify the KiCad footprint path for a package. The `kicad_mod` path is relative
to the KiCad footprints folder. Use `GET /footprints` to list current mappings.
Changes are in-memory until `save` is called.

#### Markers (viewer annotations)

```json
{"action": "mark", "x": 23.5, "y": 8.0, "color": "#ff0000", "label": "here", "size": 2}
```
Place a colored dot with optional label. `size` scales dot, font, and line (1=normal, 2=double).
Optional `lx`/`ly` draws a line from dot to that point.

```json
{"action": "clear_marks"}
```
Remove all markers.

#### Highlight

```json
{"action": "highlight", "net": "SPI_NSS"}
{"action": "highlight", "net": "GND", "color": "#ff00ff"}
{"action": "highlight", "net": ""}
```
Highlight a net in the viewer. Uses SSE (Server-Sent Events) for instant push
to the viewer — no page reload, no version increment. All tracks/pads/vias
on the net are drawn in the highlight color (default yellow `#ffff00`).
Send `"net": ""` to clear. Mouse hover temporarily overrides the highlight.

#### KiCad Capture & Push

```json
{"action": "capture_kicad"}
{"action": "capture_kicad", "socket": "ipc:///tmp/kicad/api-41011.sock"}
```
Capture board state from running KiCad PCB editor via IPC API. Populates pads, tracks,
vias, nets, components, and generates a copper heatmap overlay. Socket auto-detects
from `/tmp/kicad/api-*.sock` if not specified.

```json
{"action": "push_kicad"}
{"action": "push_kicad", "socket": "ipc:///tmp/kicad/api-41011.sock"}
```
Push all tracks and vias from server state to KiCad. Coordinates are converted back
to absolute KiCad coordinates using the origin saved during capture.

#### DRC (Design Rule Check)

Requires the server to be started with `--board path/to/board.kicad_pcb`.
Uses `kicad-cli pcb drc` on the host.

```json
{"action": "drc"}
{"action": "drc", "severity": "error"}
{"action": "drc", "all_track_errors": true, "refill_zones": true}
{"action": "drc", "schematic_parity": true}
```
- `severity`: `"all"` (default), `"error"`, `"warning"`, or `"exclusions"`
- `all_track_errors`: report all errors per track
- `schematic_parity`: include schematic parity check
- `refill_zones`: refill zones before running DRC

Also available as `GET /drc` for a quick default check.

#### Open Board

```json
{"action": "open_board", "path": "/home/ric/projects/pcb_design/kicad_examples/hackrf-one.kicad_pcb"}
```
Kills any running KiCad, launches pcbnew with the file, waits for IPC API,
then captures the board. Path must be on the host filesystem.

#### Other

```json
{"action": "save"}
{"action": "reload"}
```

## Routing Strategy

**0. Hand-place first, auto-route last.** Traces are placed as explicit
`add_track` segments, chosen by reasoning about pad geometry, corridors,
keep-outs, and `route_examples` matches. The A\* `route` / `route_tap`
actions are a last resort, not the default — their output is usually
low-quality and produces tangles across adjacent nets. `route` is
permitted only for trivial ≤2-pad nets in open regions, or after
hand-placement has been tried and documented as infeasible for the
specific net. **Never "polish" a working auto-route by replacing it with
hand-drawn segments** — the router's corners are navigating constraints
you cannot see in the render. See `CLAUDE.md` in this directory for the
full rule and an example failure.

1. **Fan-out from IC pads** — short stubs perpendicular to IC edge before routing to destination. Prevents traces running parallel along pad rows blocking access.

2. **Route order matters** — route innermost/most-constrained traces first. Outer traces route around them.

3. **Stub + via + inner/back layer pattern** for GND:
   - Short F.Cu stub from pad
   - `place_via` at stub endpoint
   - B.Cu trace between vias
   - `place_via` at other end
   - F.Cu stub to next pad

4. **Use `use8: true`** for 45-degree traces after fan-out stubs.

5. **Waypoint routing** — for traces that must avoid IC pin rows, route to a waypoint below/above the IC extent first, then to destination.

6. **Default margin=3** (0.3mm clearance). Reduce to 2 for tight areas if needed, but verify board house minimums.

7. **Save + reload after rotate/move** before routing — grid must be rebuilt with updated pad positions.

8. **No traces between U4 (RA01) pins** — pads too close. Fan out with stubs away from IC body.

9. **Graduated fan-out** — when multiple stubs fan out from an IC, graduate
   lengths so the top stub is longest. Spreads corridors for the router.

10. **T-junctions via nearest_track** — use `/nearest_track` to find closest
    point on existing trace, route branch to that point. Cleaner than routing
    two branches to the same pad.

11. **Chamfering** — replace right-angle corners with 45-degree segments.
    dx must equal dy for true diagonal (e.g. 2mm H + 2mm V = one diagonal).
    Use `use8: true` for the chamfer segment.

12. **Stubs before auto-route** — always create explicit stubs away from IC
    pins before letting the auto-router find the path. Prevents traces running
    between pins.

## Diagnostics

- `GET /clearance` — audit trace quality, fix worst-first
- `GET /density` — spatial awareness before routing, find clear corridors
- `GET /orphan_vias` — find unconnected vias after routing
- `GET /get_transitions` — verify vias at layer transitions
- `GET /nearest_track` — find T-junction points for branch routing
- `POST mark/clear_marks` — annotate viewer to communicate areas of interest

## Route Reconstruction

The board state stores tracks as a flat, unordered list of segments. `rebuild_routes.py`
reconstructs logical routes (pad-to-pad, pad-to-trace, trace-to-trace) by building a
graph from the segments and walking it.

### How It Works

1. **Group** segments by net name
2. **Build graph** — nodes are segment endpoints snapped to 0.05mm tolerance, edges are segments
3. **Classify nodes** — pad nodes (near a pad position), junction nodes (degree >= 3),
   terminal nodes (degree 1), pass-through nodes (degree 2)
4. **Walk chains** between pad/junction/terminal nodes to extract individual routes

### Usage

```python
from rebuild_routes import rebuild_routes, register_rebuilt_routes, print_routes

# Standalone — returns list of route dicts
routes = rebuild_routes(state["tracks"], state["pads"], state["vias"])
print_routes(routes)

# Register into a RouteSet (for pushout, unroute, re-route)
count = register_rebuilt_routes(routeset, grid, state)
```

### Route Dict Format

```json
{
  "net": "GND",
  "type": "pin_to_pin",
  "from_pad": {"ref": "U2", "pin": "43"},
  "to_pad": {"ref": "C6", "pin": "1"},
  "from_pt": [41.4, 40.3],
  "to_pt": [37.8, 45.3],
  "segments": [...],
  "vias": [...],
  "length_mm": 8.2,
  "layers": ["B.Cu", "F.Cu"]
}
```

- `type`: `"pin_to_pin"`, `"pin_to_trace"` (T-junction), or `"trace_to_trace"`
- `from_pad`/`to_pad`: pad identity if endpoint is at a pad, else `null`
- `from_pt`/`to_pt`: mm coordinates of route endpoints
- `segments`: the actual track segment dicts belonging to this route
- `vias`: vias used along this route
- `length_mm`: total trace length
- `layers`: copper layers used

### Route Types

| Type | Meaning |
|------|---------|
| `pin_to_pin` | Direct connection between two pads |
| `pin_to_trace` | Pad connects to an existing trace (T-junction) |
| `trace_to_trace` | Segment between two junctions (no pad at either end) |

GND nets typically show many `pin_to_trace` routes (short stubs from pads to a
shared trunk). Signal nets are usually `pin_to_pin`.

## Component Info

`component_info.py` provides component and pin lookups from KiCad via IPC.

### Usage

```python
from component_info import ComponentInfo
from grab_layer import find_socket

info = ComponentInfo(find_socket())

info.component("U2")
# {'ref': 'U2', 'value': 'ATMEGA32U4',
#  'footprint': 'Package_QFP:TQFP-44_10x10mm_P0.8mm',
#  'description': '', 'datasheet': '', 'x': 47.1, 'y': 43.5,
#  'rotation': 0.0, 'mounting': 'smd', 'pins': ['1','2',...,'44']}

info.pad("U2", "43")
# {'ref': 'U2', 'pin': '43', 'net': 'GND', 'x': 41.4, 'y': 40.3,
#  'smd': True, 'value': 'ATMEGA32U4',
#  'footprint': 'Package_QFP:TQFP-44_10x10mm_P0.8mm'}

info.components()         # all components, sorted by ref
info.find(value="10K")    # search by value substring
info.find(footprint="0603")  # search by footprint substring
```

### Fields

| Method | Returns |
|--------|---------|
| `component(ref)` | ref, value, footprint, description, datasheet, x, y, rotation, mounting, pins |
| `pad(ref, pin)` | ref, pin, net, x, y, smd + component value/footprint/description |
| `components()` | sorted list of all components |
| `find(value=, footprint=)` | components matching substring filters |

## Route Examples Database

`route_examples.py` provides semantic search over a ChromaDB collection of routes
from real open-source PCB designs. Built by `utilities/learning/collect_all_boards.py`.

### Endpoints

| Path | Returns |
|------|---------|
| `/route_examples?q=SPI+clock&n=5` | Semantic search, returns matching routes with metadata and segments |
| `/route_examples?q=decoupling&board=hackrf-one` | Filter to specific board |
| `/pad_info?ref=U1&pin=3` | Pad details: net, position, component value, footprint |
| `/component_info?ref=U1` | Component details: value, footprint, pins, position, mounting |

### Route Example Format

Each result includes:
- `description` — text used for embedding search
- `metadata` — net, board, type, length, layers, vias, from/to refs, component values
- `segments` — JSON string of `[[x1,y1,x2,y2,layer], ...]` — actual route geometry
- `vias` — JSON string of `[[x,y], ...]`
- `distance` — search relevance (lower = better match)

### Usage Pattern

Before routing a net:
1. Query pad info for both endpoints
2. Search examples for similar connections (component types, net names, footprints)
3. Parse segment geometry from matching examples — analyse departure angles, shapes, via placement
4. Apply patterns to current board

### Building the Database

```bash
export KICAD_HOST_PREFIX=/home/user/projects/pcb_design
cd utilities/learning
python3 collect_all_boards.py
```

Processes all `.kicad_pcb` files, rebuilds routes, indexes into ChromaDB at
`utilities/learning/route_collection/`.

## Route Planner

`route_planner.py` analyses the board before routing and produces a plan.

### What It Computes

| Analysis | Output |
|----------|--------|
| Components | Bounding boxes, pad counts, centers |
| Net requirements | Pad positions, distances, component refs |
| Corridors | Clear horizontal/vertical channels with no pads |
| Conflicts | Pairs of nets whose direct paths cross |
| Constraint scores | Difficulty ranking (distance, pad count, options) |
| Layer assignment | F.Cu/B.Cu per net to minimise conflicts |
| Fan-out directions | Perpendicular to IC body edge for each pin |
| Route order | Most constrained first, power nets last |

### Usage

```python
from route_planner import plan_routes, print_plan

plan = plan_routes(state)
print_plan(plan)
```

Or via command line: `python3 route_planner.py`

## Board Renderer

`board_render.py` renders the board state to a PNG image for visual feedback.

### Endpoint

```
GET /render?x1=10&y1=15&x2=30&y2=25&w=600
```

Returns PNG image. Parameters:
- `x1,y1,x2,y2` — viewport in board mm coords (omit for full board)
- `w` or `width` — output image width in pixels (default 600)

### Usage

```python
from board_render import render_board, save_render

# Full board
png_bytes = render_board(state, width=800)

# Zoomed to area of interest
png_bytes = render_board(state, x1=10, y1=15, x2=30, y2=25, width=600)

# Save to file
save_render(state, "/tmp/board.png", width=800)
```

### Visual Feedback for Routing

The agent should capture a render at key decision points during routing:
- Once at the start to understand overall layout
- After completing each net cluster to verify quality
- When debugging clearance violations

A low-res render is ~1000-2000 tokens — negligible in a 1M context window.

## KiCad IPC Integration

The server can capture board state directly from a running KiCad PCB editor using the
IPC API via the `kipy` library (`pip install kicad-python`).

### Socket Selection

KiCad creates multiple sockets in `/tmp/kicad/`:
- `api.sock` — KiCad launcher (NO board commands)
- `api-XXXXX.sock` — PCB Editor (HAS board commands) ← use this one

The `/capture_kicad` endpoint auto-detects the numbered socket.

Some KiCad versions only create `api.sock` even for the PCB editor. If no numbered
socket exists but the PCB editor is listening at `api.sock`, pass it explicitly:
`/capture_kicad?socket=ipc:///tmp/kicad/api.sock`

### Copper Heatmap

The capture rasterizes pad and via copper shapes onto 0.1mm pitch grids for all
detected copper layers. Tracks are NOT rasterized (the 0.1mm grid makes them
appear too thick) — tracks are drawn from vector data in the viewer.

- Pad polygons via `board.get_pad_shapes_as_polygons()`
- Via circles with actual diameter

Colors assigned by stack position (front=red, inner=yellow/green, back=blue).
Multiple-layer overlap is blended. Toggle with the "Pads" button in the viewer.

### grab_layer.py Functions

| Function | Returns |
|----------|---------|
| `find_socket()` | Auto-detect KiCad PCB editor socket path (falls back to api.sock) |
| `get_copper_layers(board)` | Ordered list of (kicad_id, name) for all copper layers |
| `capture_board(socket, pitch_mm)` | Full board state: pads, tracks, vias, nets, footprints, copper_grids |
| `get_copper_grids(board, pitch_mm)` | Dict of {layer_name: numpy array} for all copper layers |
| `grid_to_png_base64(copper_grids, bounds)` | Generate heatmap PNG as base64 data URL |

### kicad_route.py Functions

| Function | Returns |
|----------|---------|
| `push_tracks(socket, tracks, origin_x, origin_y)` | Push track segments to KiCad |
| `push_vias(socket, vias, origin_x, origin_y)` | Push vias to KiCad |
| `push_routes(socket, tracks, vias, origin_x, origin_y)` | Push both in single commit |
| `delete_tracks(socket, net_name=None)` | Delete tracks from KiCad (all or by net) |

### Workflow

```
KiCad ──capture_kicad──► Server ──route/analyze──► Server ──push_kicad──► KiCad
                              │                         │
                              └── RouterGrid built ◄────┘
                                  for A* routing
```

Re-capture after push to sync state. Delete tracks from KiCad if needed before re-routing.

### Saving the Board

Save the board via kipy after confirmed route changes:

```python
from kipy import KiCad
kicad = KiCad(socket_path="ipc:///tmp/kicad/api.sock")
board = kicad.get_board()
board.save()
```

KiCad IPC edits are in-memory until saved. Save after each successful
routing section is confirmed visually.
