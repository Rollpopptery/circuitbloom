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

Interactive viewer with:
- Right-click drag to pan, left-click drag to move components
- Mouse wheel zoom (preserved across reloads via sessionStorage)
- Hover shows pad info + cursor coordinates (x, y) in mm
- Component outlines drawn as white rectangles
- R key rotates grabbed component 90 degrees clockwise
- Auto-reloads on state change (polls /version)

POST `/api`:
- `{action: "move", ref, dw, dh}` — relative grid-cell move
- `{action: "rotate", ref}` — 90 degree clockwise rotation

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
| `/save` | Save tracks/vias/placement to bloom file |
| `/reload` | Reload bloom file and rebuild grid |

### POST Actions

All POST to `/` with JSON body `{action: "...", ...}`.

#### Routing

```json
{"action": "route", "net": "VCC_3V3", "from": [x1,y1], "to": [x2,y2],
 "margin": 3, "layer": "auto", "use8": true, "width": 0.25}
```
- `margin`: clearance in grid cells (3 = 0.3mm). Always use 3.
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
Remove all tracks and vias for a net.

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

#### Via

```json
{"action": "place_via", "net": "GND", "x": 10.3, "y": 34.0}
```
Place a via at a specific point. Use `/orphan_vias` to check for unconnected vias.

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

6. **Always margin=3** (0.3mm clearance). Don't reduce margin to force routes.

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
