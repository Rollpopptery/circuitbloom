#!/usr/bin/env python3
"""
dpcb_router.py — Grid-based A* PCB router for .dpcb boards.

Operates on a 0.1mm pitch integer grid. Supports N copper layers (default 2).

Key optimisation: obstacles are pre-dilated by (track_half_width + clearance)
so A* only checks single cells, not track width at every step.

Usage:
    from dpcb_router import RouterGrid, route, route_by_name
"""

import heapq
import numpy as np
from dataclasses import dataclass, field


from dpcb_router_grid import RouterGrid, GRID_PITCH, _line_cells




LAYER_FCU = 0
LAYER_BCU = 1
# Default 2-layer mappings (overridden by RouterGrid instance for N-layer boards)
LAYER_NAMES = {0: 'F.Cu', 1: 'B.Cu'}
LAYER_IDS = {'F.Cu': 0, 'B.Cu': 1}


def make_layer_maps(num_layers, custom_names=None):
    """Build layer_names and layer_ids dicts for N layers.

    Default naming: F.Cu=0, In1.Cu=1, In2.Cu=2, ..., B.Cu=last.
    custom_names: optional dict {index: name} to override defaults.
    """
    names = {}
    if num_layers == 1:
        names[0] = 'F.Cu'
    elif num_layers == 2:
        names = {0: 'F.Cu', 1: 'B.Cu'}
    else:
        names[0] = 'F.Cu'
        for i in range(1, num_layers - 1):
            names[i] = f'In{i}.Cu'
        names[num_layers - 1] = 'B.Cu'
    if custom_names:
        names.update(custom_names)
    ids = {v: k for k, v in names.items()}
    return names, ids


@dataclass
class RouteResult:
    success: bool
    path: list = field(default_factory=list)
    length_mm: float = 0.0
    via_count: int = 0
    segment_count: int = 0
    message: str = ""
    tap_point: tuple = None  # (x_mm, y_mm) where route tapped into existing trace



# ============ A* ============

def _flood_same_net(grid, net_id, gx, gy, layers):
    W, H = grid.width, grid.height
    visited = set()
    queue = []
    for layer in layers:
        if 0 <= gx < W and 0 <= gy < H and grid.occupy[layer][gy, gx] == net_id:
            key = (gx, gy, layer)
            if key not in visited:
                visited.add(key)
                queue.append(key)
    head = 0
    while head < len(queue):
        cx, cy, cl = queue[head]
        head += 1
        for nx, ny in ((cx+1,cy),(cx-1,cy),(cx,cy+1),(cx,cy-1)):
            if 0 <= nx < W and 0 <= ny < H and grid.occupy[cl][ny, nx] == net_id:
                key = (nx, ny, cl)
                if key not in visited:
                    visited.add(key)
                    queue.append(key)
        for ol in layers:
            if ol != cl and grid.occupy[ol][cy, cx] == net_id:
                key = (cx, cy, ol)
                if key not in visited:
                    visited.add(key)
                    queue.append(key)
    return visited


def route(grid, x1_mm, y1_mm, x2_mm, y2_mm, net_id,
          layer_mode='auto', via_cost=30, track_width_cells=2,
          start_layer=None, end_layer=None, margin_override=None):
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    gx2, gy2 = grid.mm_to_grid(x2_mm, y2_mm)

    if not grid.in_bounds(gx1, gy1):
        return RouteResult(False, message=f"Start out of bounds")
    if not grid.in_bounds(gx2, gy2):
        return RouteResult(False, message=f"End out of bounds")

    if layer_mode == 'auto':
        allowed = set(range(grid.num_layers))
    elif layer_mode in grid.layer_ids:
        allowed = {grid.layer_ids[layer_mode]}
    else:
        allowed = set(range(grid.num_layers))

    # Constrain start/end layers if specified (for SMD pads)
    start_layers = {start_layer} if start_layer is not None else allowed
    end_layers = {end_layer} if end_layer is not None else allowed

    margin = margin_override if margin_override is not None else 1
    blocked = grid.build_blocked_grid(net_id, margin,
                                      track_half_width=track_width_cells // 2)

    W, H = grid.width, grid.height

    counter = 0
    open_set = []
    g_score = {}
    came_from = {}

    abs_ = abs  # local reference for speed

    def h(x, y):
        return abs_(x - gx2) + abs_(y - gy2)

    sources = _flood_same_net(grid, net_id, gx1, gy1, start_layers & allowed)
    if not sources:
        sources = {(gx1, gy1, sl) for sl in start_layers if sl in allowed}
    for sgx, sgy, sl in sources:
        if not blocked[sl][sgy, sgx]:
            key = (sgx, sgy, sl)
            if key not in g_score:
                g_score[key] = 0
                heapq.heappush(open_set, (h(sgx, sgy), counter, sgx, sgy, sl))
                counter += 1

    manhattan = abs(gx2 - gx1) + abs(gy2 - gy1)
    max_iter = max(5000, manhattan * 100)
    goal_key = None
    iterations = 0
    best_h = float('inf')
    best_cell = (gx1, gy1, 0)

    while open_set and iterations < max_iter:
        iterations += 1
        f, _, cx, cy, cl = heapq.heappop(open_set)
        ck = (cx, cy, cl)
        ch = h(cx, cy)
        if ch < best_h:
            best_h = ch
            best_cell = (cx, cy, cl)

        if cx == gx2 and cy == gy2 and cl in end_layers:
            goal_key = ck
            break

        cg = g_score.get(ck)
        if cg is None or f > cg + h(cx, cy) + 1:
            continue

        # 4 neighbours
        for nx, ny in ((cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)):
            if nx < 0 or ny < 0 or nx >= W or ny >= H:
                continue
            is_goal = (nx == gx2 and ny == gy2)
            if not is_goal and blocked[cl][ny, nx]:
                continue
            ng = cg + 1
            nk = (nx, ny, cl)
            old = g_score.get(nk)
            if old is None or ng < old:
                g_score[nk] = ng
                heapq.heappush(open_set, (ng + h(nx, ny), counter, nx, ny, cl))
                counter += 1
                came_from[nk] = ck

        # Via (through-hole: connects all layers)
        if len(allowed) > 1:
            is_goal = (cx == gx2 and cy == gy2)
            goal_via_ok = is_goal and end_layer is None
            pad_keepout_ok = (cx, cy) not in grid.pad_keepout
            for ol in allowed:
                if ol == cl:
                    continue
                via_ok = not blocked[ol][cy, cx] and pad_keepout_ok
                if goal_via_ok or via_ok:
                    ng = cg + via_cost
                    vk = (cx, cy, ol)
                    old = g_score.get(vk)
                    if old is None or ng < old:
                        g_score[vk] = ng
                        heapq.heappush(open_set, (ng + h(cx, cy), counter, cx, cy, ol))
                        counter += 1
                        came_from[vk] = ck

    if goal_key is None:
        bx, by, bl = best_cell
        bx_mm, by_mm = grid.grid_to_mm(bx, by)
        bl_name = grid.layer_names.get(bl, str(bl))
        return RouteResult(False, message=f"No path found ({iterations} iters) — closest: {bl_name} ({bx_mm},{by_mm})")

    # Reconstruct
    path = []
    key = goal_key
    while key is not None:
        path.append(key)
        key = came_from.get(key)
    path.reverse()

    via_count = 0
    length_cells = 0
    for i in range(1, len(path)):
        if path[i-1][2] != path[i][2]:
            via_count += 1
        else:
            length_cells += abs(path[i][0]-path[i-1][0]) + abs(path[i][1]-path[i-1][1])

    length_mm = length_cells * GRID_PITCH

    seg_count = _count_segments(path)

    return RouteResult(
        success=True, path=path,
        length_mm=length_mm, via_count=via_count,
        segment_count=seg_count,
        message=f"Routed: {length_mm:.1f}mm, {via_count} vias, {seg_count} segs"
    )


def _count_segments(path):
    """Count track segments in a path (direction or layer changes)."""
    if len(path) < 2:
        return 0
    count = 1
    for i in range(2, len(path)):
        if path[i][2] != path[i - 1][2]:
            count += 1
            continue
        if path[i - 1][2] != path[i - 2][2]:
            continue
        dx = path[i][0] - path[i - 1][0]
        dy = path[i][1] - path[i - 1][1]
        pdx = path[i - 1][0] - path[i - 2][0]
        pdy = path[i - 1][1] - path[i - 2][1]
        if dx != pdx or dy != pdy:
            count += 1
    return count


def route_tap(grid, x1_mm, y1_mm, net_id,
              layer_mode='auto', via_cost=30, track_width_cells=2,
              start_layer=None, margin_override=None):
    """Route from a pad to the nearest existing trace on the same net.

    Like route() but the goal is any existing same-net cell on the grid
    that is NOT flood-connected to the start pad. Uses Dijkstra (h=0)
    since there's no single target point.
    """
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)

    if not grid.in_bounds(gx1, gy1):
        return RouteResult(False, message="Start out of bounds")

    if layer_mode == 'auto':
        allowed = set(range(grid.num_layers))
    elif layer_mode in grid.layer_ids:
        allowed = {grid.layer_ids[layer_mode]}
    else:
        allowed = set(range(grid.num_layers))

    start_layers = {start_layer} if start_layer is not None else allowed

    margin = margin_override if margin_override is not None else 1
    blocked = grid.build_blocked_grid(net_id, margin,
                                      track_half_width=track_width_cells // 2)

    W, H = grid.width, grid.height

    # Find cells already connected to the start pad (flood)
    start_flood = _flood_same_net(grid, net_id, gx1, gy1, start_layers & allowed)

    # Check there are same-net cells NOT in the start flood (i.e. existing traces to tap into)
    has_target = False
    for layer in range(grid.num_layers):
        if layer not in allowed:
            continue
        if np.any((grid.occupy[layer] == net_id) & ~np.isin(
                np.arange(H * W).reshape(H, W) // W * W + np.arange(W),
                [gy * W + gx for gx, gy, l in start_flood if l == layer])):
            has_target = True
            break

    # Simpler check: just verify there are same-net cells on the grid
    # that aren't in our flood
    flood_set = start_flood
    if not flood_set:
        flood_set = {(gx1, gy1, sl) for sl in start_layers if sl in allowed}

    counter = 0
    open_set = []
    g_score = {}
    came_from = {}

    for sgx, sgy, sl in flood_set:
        if not blocked[sl][sgy, sgx]:
            key = (sgx, sgy, sl)
            if key not in g_score:
                g_score[key] = 0
                heapq.heappush(open_set, (0, counter, sgx, sgy, sl))
                counter += 1

    max_iter = 50000
    goal_key = None
    goal_mm = None
    iterations = 0
    best_dist = float('inf')
    best_cell = (gx1, gy1, 0)

    while open_set and iterations < max_iter:
        iterations += 1
        f, _, cx, cy, cl = heapq.heappop(open_set)
        ck = (cx, cy, cl)

        # Goal: any same-net cell NOT in our start flood
        if grid.occupy[cl][cy, cx] == net_id and ck not in flood_set:
            goal_key = ck
            goal_mm = grid.grid_to_mm(cx, cy)
            break

        cg = g_score.get(ck)
        if cg is None or f > cg + 1:
            continue

        # Track best cell (closest to any same-net cell)
        if cg < best_dist:
            best_dist = cg
            best_cell = (cx, cy, cl)

        # 4 neighbours
        for nx, ny in ((cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)):
            if nx < 0 or ny < 0 or nx >= W or ny >= H:
                continue
            # Allow entering same-net cells (that's our goal)
            if blocked[cl][ny, nx] and grid.occupy[cl][ny, nx] != net_id:
                continue
            ng = cg + 1
            nk = (nx, ny, cl)
            old = g_score.get(nk)
            if old is None or ng < old:
                g_score[nk] = ng
                heapq.heappush(open_set, (ng, counter, nx, ny, cl))
                counter += 1
                came_from[nk] = ck

        # Via (through-hole: connects all layers)
        if len(allowed) > 1:
            pad_keepout_ok = (cx, cy) not in grid.pad_keepout
            for ol in allowed:
                if ol == cl:
                    continue
                if not blocked[ol][cy, cx] and pad_keepout_ok:
                    ng = cg + via_cost
                    vk = (cx, cy, ol)
                    old = g_score.get(vk)
                    if old is None or ng < old:
                        g_score[vk] = ng
                        heapq.heappush(open_set, (ng, counter, cx, cy, ol))
                        counter += 1
                        came_from[vk] = ck

    if goal_key is None:
        bx, by, bl = best_cell
        bx_mm, by_mm = grid.grid_to_mm(bx, by)
        bl_name = grid.layer_names.get(bl, str(bl))
        return RouteResult(False, message=f"No path to net ({iterations} iters) — closest: {bl_name} ({bx_mm},{by_mm})")

    # Reconstruct
    path = []
    key = goal_key
    while key is not None:
        path.append(key)
        key = came_from.get(key)
    path.reverse()

    via_count = sum(1 for i in range(1, len(path)) if path[i-1][2] != path[i][2])
    length_cells = sum(abs(path[i][0]-path[i-1][0]) + abs(path[i][1]-path[i-1][1])
                       for i in range(1, len(path)) if path[i-1][2] == path[i][2])
    length_mm = length_cells * GRID_PITCH
    seg_count = _count_segments(path)

    return RouteResult(
        success=True, path=path,
        length_mm=length_mm, via_count=via_count,
        segment_count=seg_count,
        message=f"Tapped: {length_mm:.1f}mm, {via_count} vias, {seg_count} segs",
        tap_point=goal_mm
    )


def route_tap_by_name(grid, net_name, x1_mm, y1_mm,
                      layer_mode='auto', margin_override=None):
    nid = grid.get_net_id(net_name)
    if not nid:
        return RouteResult(False, message=f"Unknown net: {net_name}")
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    start_layer = _find_pad_layer(grid, gx1, gy1)
    return route_tap(grid, x1_mm, y1_mm, nid,
                     layer_mode=layer_mode,
                     start_layer=start_layer,
                     margin_override=margin_override)


def _find_pad_layer(grid, gx, gy, search_r=2):
    """Look up pad layer, searching nearby cells for floating-point rounding mismatches."""
    result = grid.pad_layers.get((gx, gy))
    if result is not None:
        return result
    # Search nearby cells (rounding can be off by 1)
    for dy in range(-search_r, search_r + 1):
        for dx in range(-search_r, search_r + 1):
            if dx == 0 and dy == 0:
                continue
            result = grid.pad_layers.get((gx + dx, gy + dy))
            if result is not None:
                return result
    return None  # not a pad or through-hole


def route_by_name(grid, net_name, x1_mm, y1_mm, x2_mm, y2_mm,
                  layer_mode='auto', via_cost=30, track_width_cells=2,
                  margin_override=None):
    nid = grid.get_net_id(net_name)
    if not nid:
        return RouteResult(False, message=f"Unknown net: {net_name}")
    # Look up pad layers for start/end points
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    gx2, gy2 = grid.mm_to_grid(x2_mm, y2_mm)
    start_layer = _find_pad_layer(grid, gx1, gy1)
    end_layer = _find_pad_layer(grid, gx2, gy2)
    return route(grid, x1_mm, y1_mm, x2_mm, y2_mm, nid,
                 layer_mode, via_cost, track_width_cells,
                 start_layer=start_layer, end_layer=end_layer,
                 margin_override=margin_override)



# ============ SELF-TEST ============

if __name__ == '__main__':
    import time
    from dpcb_pathset import RouteSet, tracks_to_dpcb_lines

    print("dpcb_router self-test (numpy)")
    print("=" * 50)

    grid = RouterGrid(100, 80, clearance_cells=2, via_od_cells=6, via_id_cells=3)
    grid.net_ids = {'clk': 1, 'data': 2, 'blocker': 3}

    for x in range(300, 500):
        for y in range(350, 450):
            grid.set_cell(0, x, y, 3)
            grid.set_cell(1, x, y, 3)

    print(f"Grid: {grid.width}x{grid.height} = {grid.width*grid.height*2:,} cells")

    routeset = RouteSet(track_width_cells=2)

    t0 = time.perf_counter()
    r1 = route(grid, 3.0, 20.0, 82.0, 12.0, 1, 'auto', 30)
    t1 = time.perf_counter()
    if r1.success:
        src = (r1.path[0][0], r1.path[0][1], r1.path[0][2])
        dst = (r1.path[-1][0], r1.path[-1][1], r1.path[-1][2])
        rid1, out1 = routeset.add_route(1, src, dst, r1.path, grid)
    print(f"Route 1: {r1.message}  ({(t1-t0)*1000:.1f}ms)")

    t0 = time.perf_counter()
    r2 = route(grid, 3.0, 22.7, 82.0, 15.0, 2, 'auto', 30)
    t1 = time.perf_counter()
    if r2.success:
        src = (r2.path[0][0], r2.path[0][1], r2.path[0][2])
        dst = (r2.path[-1][0], r2.path[-1][1], r2.path[-1][2])
        rid2, out2 = routeset.add_route(2, src, dst, r2.path, grid)
    print(f"Route 2: {r2.message}  ({(t1-t0)*1000:.1f}ms)")

    if r1.success:
        print(f"\nDPCB output:")
        for line in tracks_to_dpcb_lines(out1):
            print(f"  {line}")

    routeset.remove_route(rid1, grid)
    t0 = time.perf_counter()
    r3 = route(grid, 3.0, 20.0, 82.0, 12.0, 1, 'F.Cu', 30)
    t1 = time.perf_counter()
    if r3.success:
        src = (r3.path[0][0], r3.path[0][1], r3.path[0][2])
        dst = (r3.path[-1][0], r3.path[-1][1], r3.path[-1][2])
        rid3, out3 = routeset.add_route(1, src, dst, r3.path, grid)
    print(f"\nReroute F.Cu only: {r3.message}  ({(t1-t0)*1000:.1f}ms)")

    # Test route info
    infos = routeset.list_routes(grid)
    print(f"\nRoutes: {len(infos)}")
    for info in infos:
        print(f"  route {info['route_id']}: net={info['net_name']} "
              f"keepouts={info['keepouts']}")

    print(f"\nStats: {grid.stats()}")
    print("=" * 50)