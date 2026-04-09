#!/usr/bin/env python3
"""
dpcb_pathset.py — Route definitions with per-route keepout zones for push-out.

A Route is a pad-to-pad connection definition:
  - source/destination pad grid coords
  - keepout zones that persist across re-routes

The grid is the single source of truth for where traces are.
The Route defines *what* to connect and *where not to go*.

Push-out works by placing keepout squares along cells that are too
close to obstacles. The A* router treats these as blocked, naturally
finding a path further from obstacles. Keepouts persist so re-routing
always respects previous push decisions.

Usage:
    from dpcb_pathset import RouteSet, tracks_to_dpcb_lines

    routeset = RouteSet(track_width_cells=2)
    route_id, tracks = routeset.add_route(net_id, src_pad, dst_pad, raw_path, grid)
    result, tracks = routeset.pushout(route_id, grid, amount=5)
"""

from dataclasses import dataclass, field
from typing import Tuple, List, Dict, Set
import numpy as np

from dpcb_router import RouterGrid, GRID_PITCH, LAYER_NAMES


# ============ DATA STRUCTURES ============

@dataclass
class Route:
    """A pad-to-pad connection definition with persistent keepout zones."""
    net_id: int
    src_pad: Tuple[int, int, int]   # (gx, gy, layer)
    dst_pad: Tuple[int, int, int]   # (gx, gy, layer)
    keepouts: Set[Tuple[int, int, int]] = field(default_factory=set)  # {(gx, gy, layer), ...}

@dataclass
class TrackSegment:
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    layer: str
    net: str
    width_mm: float

@dataclass
class ViaPoint:
    x_mm: float
    y_mm: float
    od_mm: float
    id_mm: float
    net: str

@dataclass
class TrackOutput:
    tracks: list = field(default_factory=list)
    vias: list = field(default_factory=list)


# ============ ROUTE SET ============

KEEPOUT_NET_ID = -1   # sentinel value for keepout cells on the grid

class RouteSet:
    """
    Manages route definitions on the board.

    The grid is the source of truth for where traces are.
    Routes define what to connect and carry keepout zones.
    """

    def __init__(self, track_width_cells=2):
        self.routes: Dict[int, Route] = {}   # route_id -> Route
        self.track_width_cells = track_width_cells
        self._next_id = 1

    # ---- route management ----

    def add_route(self, net_id, src_pad, dst_pad, raw_path, grid, track_width_mm=0.2,
                  start_mm=None, end_mm=None):
        """
        Create a route from an A* result. Marks the path on grid,
        returns (route_id, TrackOutput).

        src_pad/dst_pad: (gx, gy, layer) tuples
        raw_path: list of (gx, gy, layer) from router
        """
        route_id = self._next_id
        self._next_id += 1

        route = Route(
            net_id=net_id,
            src_pad=src_pad,
            dst_pad=dst_pad,
        )
        self.routes[route_id] = route

        # Find where pre-existing same-net cells end in the path.
        # The path may start by re-traversing already-placed cells before
        # reaching new territory. Clip to start at the last pre-existing cell
        # (the junction point) so the output connects cleanly without re-tracing.
        first_new = 0
        for idx, (gx, gy, layer) in enumerate(raw_path):
            if grid.occupy[layer][gy, gx] == net_id:
                first_new = idx
            else:
                break

        # Mark path on grid
        _mark_path_on_grid(raw_path, net_id, grid, self.track_width_cells)

        # Generate track output starting from the junction (last pre-existing cell)
        net_name = _net_name_from_id(grid, net_id)
        output = TrackOutput()
        _raw_path_to_output(raw_path[first_new:], net_name, grid, track_width_mm, output,
                            start_mm=start_mm, end_mm=end_mm)

        # Check for via at the clipping boundary — a layer change between
        # the last pre-existing cell and the first new cell may have been
        # clipped out of the path passed to _raw_path_to_output.
        if first_new > 0 and first_new < len(raw_path):
            prev_layer = raw_path[first_new - 1][2]
            curr_layer = raw_path[first_new][2]
            if prev_layer != curr_layer:
                gx, gy = raw_path[first_new][0], raw_path[first_new][1]
                vx, vy = grid.grid_to_mm(gx, gy)
                output.vias.append(ViaPoint(
                    vx, vy,
                    grid.via_od * GRID_PITCH,
                    grid.via_id * GRID_PITCH,
                    net_name
                ))

        return route_id, output

    def register_route(self, net_id, src_pad, dst_pad):
        """Register a pre-existing route (from .dpcb file) without marking the grid.
        The grid is already populated by populate_from_board()."""
        route_id = self._next_id
        self._next_id += 1
        route = Route(net_id=net_id, src_pad=src_pad, dst_pad=dst_pad)
        self.routes[route_id] = route
        return route_id

    def remove_route(self, route_id, grid):
        """Remove a route: clear its net cells from grid, delete route."""
        route = self.routes.get(route_id)
        if not route:
            return False
        # Clear all cells for this net from grid
        _clear_net_from_grid(route.net_id, grid)
        # Re-mark other routes that share this net
        for rid, r in self.routes.items():
            if rid != route_id and r.net_id == route.net_id:
                # Can't re-mark without path — other routes' cells are gone.
                # For now, this is only safe when removing all routes for a net.
                pass
        del self.routes[route_id]
        return True

    def remove_routes_for_net(self, net_id, grid):
        """Remove all routes for a net. Returns count removed."""
        to_remove = [rid for rid, r in self.routes.items() if r.net_id == net_id]
        if to_remove:
            _clear_net_from_grid(net_id, grid)
        for rid in to_remove:
            del self.routes[rid]
        return len(to_remove)

    def remove_by_name(self, net_name, grid):
        """Remove all routes for a named net."""
        nid = grid.get_net_id(net_name)
        return self.remove_routes_for_net(nid, grid) if nid else 0

    def get_routes_for_net(self, net_id):
        """Return list of (route_id, Route) for a net."""
        return [(rid, r) for rid, r in self.routes.items() if r.net_id == net_id]

    # ---- pushout ----

    def pushout(self, route_id, grid, amount=5, pad_margin=3, keepout_radius=2,
                track_width_mm=0.2):
        """
        One iteration of push-out for a single route.

        1. Scan grid cells for this net — find close-to-obstacle cells
        2. Place keepout squares at those cells (not near pads)
        3. Clear net from grid
        4. Apply all keepouts to grid
        5. Re-route pad-to-pad (A* avoids keepouts)
        6. Remove keepouts from grid (keep in Route)
        7. Mark new path on grid

        Returns (stats_dict, TrackOutput or None).
        """
        route = self.routes.get(route_id)
        if not route:
            return {'error': 'route not found'}, None

        # Step 1: find net cells on grid that are close to obstacles
        net_cells = _get_net_cells(route.net_id, grid)
        close_cells = _scan_cells_for_obstacles(
            net_cells, grid, route.net_id, threshold=amount * 2
        )

        # Step 2: create keepout squares, avoiding pad areas
        new_keepouts = _create_keepouts(
            close_cells, route, grid, keepout_radius, pad_margin
        )
        added = len(new_keepouts - route.keepouts)
        route.keepouts |= new_keepouts

        if not route.keepouts:
            return {
                'close': len(close_cells),
                'new_keepouts': 0,
                'total_keepouts': 0,
                'success': True,
                'message': 'no obstacles nearby, nothing to push',
            }, None

        # Step 3: clear net from grid
        _clear_net_from_grid(route.net_id, grid)

        # Step 4: apply keepouts to grid
        _apply_keepouts(route.keepouts, grid)

        # Step 5: re-route pad-to-pad
        from dpcb_router import route as do_route
        sx, sy, sl = route.src_pad
        dx, dy, dl = route.dst_pad
        sx_mm, sy_mm = grid.grid_to_mm(sx, sy)
        dx_mm, dy_mm = grid.grid_to_mm(dx, dy)

        result = do_route(grid, sx_mm, sy_mm, dx_mm, dy_mm, route.net_id,
                          start_layer=sl, end_layer=dl)

        # Step 6: remove keepouts from grid (keep in Route)
        _unapply_keepouts(route.keepouts, grid)

        # Step 7: mark new path on grid, generate output
        if result.success:
            _mark_path_on_grid(result.path, route.net_id, grid,
                               self.track_width_cells)
            net_name = _net_name_from_id(grid, route.net_id)
            output = TrackOutput()
            _raw_path_to_output(result.path, net_name, grid, track_width_mm, output)
            return {
                'close': len(close_cells),
                'new_keepouts': added,
                'total_keepouts': len(route.keepouts),
                'success': True,
                'message': f're-routed OK, {added} new keepouts',
            }, output
        else:
            # Route failed — net is unrouted, keepouts remain for caller to decide
            return {
                'close': len(close_cells),
                'new_keepouts': added,
                'total_keepouts': len(route.keepouts),
                'success': False,
                'message': f're-route failed: {result.message}',
            }, None

    def clear_keepouts(self, route_id):
        """Remove all keepout zones for a route."""
        route = self.routes.get(route_id)
        if route:
            route.keepouts.clear()

    # ---- track output from grid ----

    def to_tracks_from_path(self, raw_path, net_id, grid, track_width_mm=0.2):
        """Generate TrackOutput from a raw A* path (not stored)."""
        net_name = _net_name_from_id(grid, net_id)
        output = TrackOutput()
        _raw_path_to_output(raw_path, net_name, grid, track_width_mm, output)
        return output

    # ---- debug / info ----

    def get_route_info(self, route_id, grid):
        """Return info dict for a route."""
        route = self.routes.get(route_id)
        if not route:
            return None
        sx, sy = grid.grid_to_mm(*route.src_pad[:2])
        dx, dy = grid.grid_to_mm(*route.dst_pad[:2])
        return {
            'route_id': route_id,
            'net_id': route.net_id,
            'net_name': _net_name_from_id(grid, route.net_id),
            'src_mm': (sx, sy),
            'dst_mm': (dx, dy),
            'keepouts': len(route.keepouts),
        }

    def list_routes(self, grid, net_id=None):
        """List all routes (or filtered by net)."""
        infos = []
        for rid in sorted(self.routes):
            route = self.routes[rid]
            if net_id is not None and route.net_id != net_id:
                continue
            infos.append(self.get_route_info(rid, grid))
        return infos


# ============ GRID OPERATIONS ============

def _mark_path_on_grid(path, net_id, grid, track_width_cells):
    """Stamp simplified path segments + via circles onto the occupancy grid.

    Simplifies the raw A* staircase path into straight segments first,
    then marks grid cells along those straight lines.  This ensures the
    grid matches the simplified track data stored in board.tracks, so
    unroute_seg can clear exactly what was drawn.
    """
    from dpcb_router import _line_cells
    half_w = track_width_cells // 2

    # Split path into same-layer runs, simplify each, mark straight lines
    i = 0
    while i < len(path):
        layer = path[i][2]
        run_start = i
        while i < len(path) and path[i][2] == layer:
            i += 1
        run = path[run_start:i]
        if len(run) < 2:
            # Single cell — mark it directly
            if run:
                gx, gy, _ = run[0]
                for dy in range(-half_w, half_w + 1):
                    for dx in range(-half_w, half_w + 1):
                        grid.set_cell(layer, gx + dx, gy + dy, net_id)
            continue
        # Simplify into straight segments
        segs = _simplify(run)
        for (sx, sy, _), (ex, ey, _) in segs:
            cells = _line_cells(sx, sy, ex, ey)
            for cx, cy in cells:
                for dy in range(-half_w, half_w + 1):
                    for dx in range(-half_w, half_w + 1):
                        grid.set_cell(layer, cx + dx, cy + dy, net_id)

    # Via circles on both layers
    for i in range(1, len(path)):
        if path[i][2] != path[i - 1][2]:
            vx, vy = path[i][0], path[i][1]
            for layer in range(grid.num_layers):
                grid.mark_circle(layer, vx, vy, grid.via_od // 2, net_id)


def _clear_net_from_grid(net_id, grid):
    """Clear ALL cells for a net from all layers using numpy."""
    for layer in range(grid.num_layers):
        grid.occupy[layer][grid.occupy[layer] == net_id] = 0


def _get_net_cells(net_id, grid):
    """
    Get all cells for a net from the grid.
    Returns list of (gx, gy, layer) — unordered.
    """
    cells = []
    for layer in range(grid.num_layers):
        ys, xs = np.where(grid.occupy[layer] == net_id)
        for x, y in zip(xs, ys):
            cells.append((int(x), int(y), layer))
    return cells


def _scan_cells_for_obstacles(cells, grid, net_id, threshold=10):
    """
    Check cells for proximity to foreign obstacles.
    Returns list of (gx, gy, layer, dist, pdx, pdy) for close cells.
    Samples to avoid redundancy on dense cell sets.
    """
    close = []
    # Sample — no need to check every single cell in the track width
    seen = set()
    for gx, gy, layer in cells:
        # Round to step grid to avoid checking overlapping track-width cells
        key = (gx // 2, gy // 2, layer)
        if key in seen:
            continue
        seen.add(key)

        dist, pdx, pdy = _find_nearest_obstacle(
            grid, gx, gy, layer, net_id, max_dist=threshold
        )
        if dist <= threshold and pdx is not None:
            close.append((gx, gy, layer, dist, pdx, pdy))
    return close


def _create_keepouts(close_cells, route, grid, radius, pad_margin):
    """
    Create keepout cell set from close cells.
    Places a square of (2*radius+1) cells centred on each close cell.
    Excludes cells within pad_margin of source or destination pads.
    """
    keepouts = set()
    sx, sy, sl = route.src_pad
    dx, dy, dl = route.dst_pad

    for gx, gy, layer, dist, pdx, pdy in close_cells:
        for ky in range(gy - radius, gy + radius + 1):
            for kx in range(gx - radius, gx + radius + 1):
                if not grid.in_bounds(kx, ky):
                    continue
                # Don't cover source pad area
                if abs(kx - sx) <= pad_margin and abs(ky - sy) <= pad_margin:
                    continue
                # Don't cover destination pad area
                if abs(kx - dx) <= pad_margin and abs(ky - dy) <= pad_margin:
                    continue
                # Don't place on cells occupied by other nets
                cell = grid.occupy[layer][ky, kx]
                if cell != 0 and cell != route.net_id:
                    continue
                keepouts.add((kx, ky, layer))

    return keepouts


def _apply_keepouts(keepouts, grid):
    """Mark keepout cells on the grid as blocked."""
    for kx, ky, layer in keepouts:
        if grid.in_bounds(kx, ky):
            cell = grid.occupy[layer][ky, kx]
            if cell == 0:
                grid.occupy[layer][ky, kx] = KEEPOUT_NET_ID


def _unapply_keepouts(keepouts, grid):
    """Remove keepout marks from the grid."""
    for kx, ky, layer in keepouts:
        if grid.in_bounds(kx, ky):
            if grid.occupy[layer][ky, kx] == KEEPOUT_NET_ID:
                grid.occupy[layer][ky, kx] = 0


# ============ OBSTACLE SCANNING ============

def _find_nearest_obstacle(grid, gx, gy, layer, net_id, max_dist=50):
    """
    Expanding circle scan from (gx, gy). Finds nearest foreign-net obstacle.

    Returns (distance, push_dx, push_dy) where push direction is a
    normalised float vector pointing AWAY from the nearest obstacle.
    Returns (max_dist+1, None, None) if nothing found.
    """
    occupy = grid.occupy[layer]
    W, H = grid.width, grid.height

    for r in range(1, max_dist + 1):
        obs_cells = []
        for ddx in range(-r, r + 1):
            for ddy in range(-r, r + 1):
                d_sq = ddx * ddx + ddy * ddy
                if d_sq > r * r:
                    continue
                if d_sq <= (r - 1) * (r - 1):
                    continue
                nx, ny = gx + ddx, gy + ddy
                if not (0 <= nx < W and 0 <= ny < H):
                    obs_cells.append((ddx, ddy))
                    continue
                cell = occupy[ny, nx]
                if cell != 0 and cell != net_id:
                    obs_cells.append((ddx, ddy))

        if obs_cells:
            avg_dx = sum(c[0] for c in obs_cells) / len(obs_cells)
            avg_dy = sum(c[1] for c in obs_cells) / len(obs_cells)
            mag = (avg_dx * avg_dx + avg_dy * avg_dy) ** 0.5
            if mag < 0.001:
                return (r, None, None)
            push_dx = -avg_dx / mag
            push_dy = -avg_dy / mag
            return (r, push_dx, push_dy)

    return (max_dist + 1, None, None)


# ============ TRACK OUTPUT ============

def _net_name_from_id(grid, net_id):
    """Look up net name from net_id."""
    for name, nid in grid.net_ids.items():
        if nid == net_id:
            return name
    return ""


def _simplify(points):
    """Collapse collinear runs into (start, end) segment pairs."""
    if len(points) < 2:
        return []
    segs = []
    start = points[0]
    pdx = points[1][0] - points[0][0]
    pdy = points[1][1] - points[0][1]
    for i in range(2, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        if dx != pdx or dy != pdy:
            segs.append((start, points[i - 1]))
            start = points[i - 1]
            pdx, pdy = dx, dy
    segs.append((start, points[-1]))
    return segs


def _raw_path_to_output(path, net_name, grid, track_width_mm, output,
                        start_mm=None, end_mm=None):
    """Append TrackSegments and ViaPoints from a raw (gx,gy,layer) path.

    start_mm/end_mm: optional (x, y) exact pad coordinates. If provided,
    the first and last track segment endpoints are snapped to these
    positions instead of grid-snapped coordinates.
    """
    if not path:
        return

    i = 0
    while i < len(path):
        gx, gy, layer = path[i]

        # Detect via (layer change)
        if i > 0 and path[i - 1][2] != layer:
            vx, vy = grid.grid_to_mm(gx, gy)
            output.vias.append(ViaPoint(
                vx, vy,
                grid.via_od * GRID_PITCH,
                grid.via_id * GRID_PITCH,
                net_name
            ))

        # Collect same-layer run
        run_start = i
        while i < len(path) and path[i][2] == layer:
            i += 1

        run = path[run_start:i]
        if len(run) < 2:
            continue

        for (sx, sy, _), (ex, ey, _) in _simplify(run):
            sx_mm, sy_mm = grid.grid_to_mm(sx, sy)
            ex_mm, ey_mm = grid.grid_to_mm(ex, ey)
            output.tracks.append(TrackSegment(
                sx_mm, sy_mm, ex_mm, ey_mm,
                grid.layer_names.get(layer, f'L{layer}'), net_name, track_width_mm
            ))

    # Snap first and last track endpoints to exact pad coordinates
    if output.tracks and start_mm:
        t = output.tracks[0]
        t.x1_mm, t.y1_mm = start_mm
    if output.tracks and end_mm:
        t = output.tracks[-1]
        t.x2_mm, t.y2_mm = end_mm


def tracks_to_dpcb_lines(output):
    """Serialise TrackOutput to .dpcb TRK/VIA lines."""
    lines = []
    for t in output.tracks:
        lines.append(
            f"TRK:({t.x1_mm},{t.y1_mm})->({t.x2_mm},{t.y2_mm})"
            f":{t.width_mm}:{t.layer}:{t.net}"
        )
    for v in output.vias:
        lines.append(f"VIA:({v.x_mm},{v.y_mm}):{v.od_mm}/{v.id_mm}:{v.net}")
    return lines
