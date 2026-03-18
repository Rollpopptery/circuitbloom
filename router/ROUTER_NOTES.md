# DPCB Router Development Notes

## Session: 2026-03-15

### Bugs Fixed

1. **Type mismatch in pad_net lookup** - `pad.num` is int, NET parsing stored pin as int, but lookup used `str(pad.num)`. Fixed to use int consistently.

2. **Unconnected pads not blocking** - Pads with `nid=0` (not in any net) were treated as empty space. Now marked as `-1` (obstacle) to block all routes.

3. **Pad radius too large** - `pad_r=4` plus routing margin exceeded TSSOP 0.65mm pitch. Reduced to `pad_r=2`.

4. **SMD pads on both layers** - SMD pads were marked on F.Cu and B.Cu. Now inferred from footprint type (`_SMD`, `Package_SO`, `SOIC`, `TSSOP`, `QFP`, `BGA`) and marked on F.Cu only.

5. **A* goal accepts any layer** - Routes could reach SMD pads from wrong layer without via. Added `start_layer` and `end_layer` parameters to `route()`. Pad layers stored in `grid.pad_layers` dict.

6. **Vias placed on pads** - Via placement allowed on same-net pads. Added `grid.pad_keepout` set with `via_keepout_r = pad_r + 2`. Vias blocked near ALL pads regardless of net.

7. **Via at goal bypassed keepout** - `is_goal` check allowed via on destination pad. Fixed to only allow via at goal for through-hole pads (`end_layer is None`).

### Routing Order Strategy

Route in this order (constrained paths first, flexible paths last):
1. SR outputs (longest routes, tight-pitch TSSOP endpoints)
2. Control signals (J1 to U1/U2)
3. Local nets (ILIM resistors, VM capacitors)
4. GND (most flexible, many paths available)

### Future Work: Track Spreading

**Goal:** Push tracks away from pads/obstacles into empty space.

**Proposed algorithm:**
```
repeat until no movement:
    for each track cell:
        direction = away_from_nearest_obstacle
        move cell 1 step in direction
        if DRC_fail():
            undo move
```

**Key considerations:**
- Process outer tracks first naturally (iterate until convergence)
- Vias move with their track cells
- Tracks may need to grow/shrink to maintain connectivity
- Straight lines stay straight (all cells move same direction)
- Curves expand (cells move radially outward)
- Movement granularity: 0.1mm (1 grid cell)
- Max expansion: ~5mm (50 cells)

**Complications to solve:**
- Moving cells can break connectivity
- May need to add/remove cells to maintain path
- Vias exist on both layers - both tracks must follow
- Consider storing tracks as ordered cell paths, move "anchors" (endpoints/vias), then heal connections between

**Simpler first step:** Analyze dpcb file to identify expansion opportunities - measure clearance to nearest obstacle, report where tracks are tight and where space is available.

## Component Placement Analysis Pattern

Before routing, check for **column conflicts** — components that share an x-column across rows will force routes through each other's bodies.

**Think pattern:**
1. Group components by row (y-band). Identify upper row, lower row, etc.
2. For each lower-row chip, check if any upper-row chip shares the same x-column (within ~3mm).
3. If yes: routes from the controller (U1/U2) to the lower-row chip must pass through the upper-row chip body — a **column conflict**.
4. Resolution: move one of the conflicting chips in x so the routing corridor runs between chips, not through them.
   - Prefer moving the chip whose existing routes are simpler (fewer segments).
   - The destination chip can shift toward the controller (smaller route angle) or away — pick whichever keeps it clear of adjacent chips.
5. Use `check_crowding` after routing to verify — a 0.0mm clearance means a track is passing through a component body. This is always a placement error, not a routing problem.

**Key insight:** routing is automatic and zero-cost. Component placement is the design decision. Always fix placement conflicts before routing, not by routing around them.

## Session: 2026-03-16

### New Module: dpcb_pathset.py

Implemented the track push-out system as `dpcb_pathset.py`. This replaces the speculative "track spreading" algorithm from the 03-15 notes with a keepout-based approach.

**How it works:**
- `RouteSet` manages a collection of `Route` objects, each a pad-to-pad connection
- Push-out adds keepout zones (blocked cells) near obstacles/pads, then re-routes through A*
- Keepouts persist across re-routes so the router doesn't snap back to tight paths
- The A* router sees keepouts as blocked cells — no changes to the core pathfinder needed

**Key classes:**
- `Route` — src/dst pad coords + persistent keepout set
- `RouteSet` — collection of routes, orchestrates add/pushout/re-route
- `TrackSegment` — output format for dpcb file track lines
- `tracks_to_dpcb_lines()` — converts grid paths to .dpcb TRACK lines

**Push-out algorithm:**
1. For each cell on the existing path, measure distance to nearest obstacle
2. If distance < threshold, place keepout square on the obstacle side
3. Unroute the track from the grid
4. Re-route with keepouts applied as additional blocked cells
5. Repeat up to `amount` iterations

### API Integration

- `dpcb_api.py` updated to use `RouteSet` and `KEEPOUT_NET_ID` from pathset
- API `route` command now creates Route objects in the RouteSet
- Push-out can be triggered via API after routing

### dpcb_router.py Changes

- `route_by_name()` function added/updated — routes by net name string, used by API
- Grid building and A* core unchanged

### Key Files

- `/workspace/utilities/router/dpcb_router.py` - Core A* router, grid management
- `/workspace/utilities/router/dpcb_viewer.py` - GUI viewer, board parsing
- `/workspace/utilities/router/dpcb_api.py` - TCP command server
- `/workspace/demo_2_7seg/test2.dpcb` - Test board (7-segment driver)
- `/workspace/demo_2_7seg/test2_clean.dpcb` - Test board without tracks
- `/workspace/demo_2_7seg/test2_routed.dpcb` - Routed result

### Grid Details

- Pitch: 0.1mm per cell
- Layers: 0=F.Cu, 1=B.Cu
- `occupy[layer][y, x]`: 0=empty, >0=net_id, -1=obstacle
- `pad_layers[(gx, gy)]`: 0=F.Cu, 1=B.Cu, None=both (through-hole)
- `pad_keepout`: set of (gx, gy) where vias blocked
