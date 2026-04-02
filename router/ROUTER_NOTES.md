# Route Server — API Reference

## Architecture

- `route_server.py` — Entry point, arg parsing, file watcher
- `route_state.py` — Shared state, bloom load/save, placement, routing helpers
- `route_handlers.py` — HTTP handlers (browser port 8083, agent port 8084)
- `route_convert.py` — Path-to-segment conversion, net colors
- `viewer.html` — Interactive HTML/Canvas viewer
- `bloom_grid.py` — Bloom file loading, grid building, pad positions
- `tree_to_xy.py` — Placement resolver (grid-cell to mm)
- `dpcb_router.py` — Core A* router, grid management
- `dpcb_router8.py` — 8-direction A* router (45-degree traces)
- `grab_layer.py` — KiCad IPC capture (pads, tracks, vias, copper grids)
- `kicad_route.py` — Push routes to KiCad (tracks, vias, delete)

## Grid

- Pitch: 0.1mm per cell
- Layers: 0=F.Cu, 1=B.Cu
- `occupy[layer][y, x]`: 0=empty, >0=net_id, -1=obstacle
- `pad_layers[(gx, gy)]`: 0=F.Cu only (SMD), None=both (through-hole)
- `pad_keepout`: set of (gx, gy) where vias are blocked

## Placement Format

Components positioned by integer grid-cell coordinates in bloom file:

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
- Toggle buttons: F.Cu, B.Cu, Labels, Ratsnest, Pads
- Pads overlay shows rasterized copper shapes (red=F.Cu, blue=B.Cu, purple=both), enabled by default
- Auto-reloads on state change (polls /version)

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
| `/clearance` | Per-net minimum clearance to nearest foreign obstacle, sorted worst-first |
| `/orphan_vias` | Vias not connected to any trace endpoint |
| `/get_vias` | All vias |
| `/get_transitions` | Layer transition points, flags missing vias |
| `/nearest_track?net=X&x=N&y=N` | Closest point on any trace of net to (x,y) — for T-junctions |
| `/footprints` | All footprint mappings `{package: {kicad_mod, pads}, ...}` |
| `/find_via_spot?net=X&x=N&y=N&margin=3` | BFS from pad to find nearest reachable via spot (clear path guaranteed) |
| `/drc` | Run KiCad DRC on the board file (requires `--board` flag) |
| `/save` | Save tracks/vias/placement to bloom file |
| `/reload` | Reload bloom file and rebuild grid |
| `/capture_kicad` | Capture board state from running KiCad (pads, tracks, vias, copper heatmap) |
| `/capture_kicad?socket=<path>` | Capture with explicit socket path (e.g. `ipc:///tmp/kicad/api-41011.sock`) |
| `/push_kicad` | Push all tracks and vias from server state to KiCad |

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
Add a single track segment manually.

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
```
Highlight a net in the viewer. Send `"net": ""` to clear.

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

#### Other

```json
{"action": "save"}
{"action": "reload"}
```

## Routing Strategy

1. **Fan-out from IC pads** — short stubs perpendicular to IC edge before routing to destination. Prevents traces running parallel along pad rows blocking access.

2. **Route order matters** — route innermost/most-constrained traces first. Outer traces route around them.

3. **Stub + via + B.Cu pattern** for GND:
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

## Routing Strategy

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

The capture rasterizes actual copper shapes onto 0.1mm pitch grids:
- Pad polygons via `board.get_pad_shapes_as_polygons()`
- Track segments with actual width
- Via circles with actual diameter

The heatmap PNG overlay shows:
- **Red** — F.Cu copper only
- **Blue** — B.Cu copper only
- **Purple** — Both layers (overlap)

Toggle with the "Pads" button in the viewer toolbar (enabled by default).

### grab_layer.py Functions

| Function | Returns |
|----------|---------|
| `find_socket()` | Auto-detect KiCad PCB editor socket path |
| `capture_board(socket, pitch_mm)` | Full board state: pads, tracks, vias, nets, footprints, fcu/bcu grids |
| `get_copper_grids(board, pitch_mm)` | F.Cu and B.Cu as numpy arrays (0=empty, 1=pad, 2=track, 3=via) |
| `grid_to_png_base64(fcu, bcu, bounds)` | Generate heatmap PNG as base64 data URL |

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
