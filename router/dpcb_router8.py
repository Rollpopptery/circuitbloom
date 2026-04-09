#!/usr/bin/env python3
"""
dpcb_router8.py — 8-direction A* PCB router for .dpcb boards.

Same grid and interface as dpcb_router.py but with diagonal movement.
Diagonal cost = sqrt(2). Heuristic = octile distance.

Usage:
    from dpcb_router8 import route8, route8_by_name
"""

import heapq
import math

from dpcb_router import RouteResult, GRID_PITCH, LAYER_NAMES

DIAG_COST = 14   # sqrt(2) * 10, scaled to int for speed
STRAIGHT_COST = 10


def _octile_h(x, y, gx2, gy2):
    dx = abs(x - gx2)
    dy = abs(y - gy2)
    return STRAIGHT_COST * (dx + dy) + (DIAG_COST - 2 * STRAIGHT_COST) * min(dx, dy)


def route8(grid, x1_mm, y1_mm, x2_mm, y2_mm, net_id,
           layer_mode='auto', via_cost=300, track_width_cells=2,
           start_layer=None, end_layer=None, margin_override=None):
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    gx2, gy2 = grid.mm_to_grid(x2_mm, y2_mm)

    if not grid.in_bounds(gx1, gy1):
        return RouteResult(False, message="Start out of bounds")
    if not grid.in_bounds(gx2, gy2):
        return RouteResult(False, message="End out of bounds")

    if layer_mode == 'auto':
        allowed = set(range(grid.num_layers))
    elif layer_mode in grid.layer_ids:
        allowed = {grid.layer_ids[layer_mode]}
    else:
        allowed = set(range(grid.num_layers))

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

    # Flood same-net cells at start (reuse logic inline)
    sources = _flood_same_net_8(grid, net_id, gx1, gy1, start_layers & allowed)
    if not sources:
        sources = {(gx1, gy1, sl) for sl in start_layers if sl in allowed}
    for sgx, sgy, sl in sources:
        if not blocked[sl][sgy, sgx]:
            key = (sgx, sgy, sl)
            if key not in g_score:
                g_score[key] = 0
                heapq.heappush(open_set, (_octile_h(sgx, sgy, gx2, gy2), counter, sgx, sgy, sl))
                counter += 1

    manhattan = abs(gx2 - gx1) + abs(gy2 - gy1)
    max_iter = max(5000, manhattan * 100)
    goal_key = None
    iterations = 0
    best_h = float('inf')
    best_cell = (gx1, gy1, 0)

    # 8 directions: 4 straight + 4 diagonal
    NEIGHBOURS = (
        (1, 0, STRAIGHT_COST), (-1, 0, STRAIGHT_COST),
        (0, 1, STRAIGHT_COST), (0, -1, STRAIGHT_COST),
        (1, 1, DIAG_COST), (-1, 1, DIAG_COST),
        (1, -1, DIAG_COST), (-1, -1, DIAG_COST),
    )

    while open_set and iterations < max_iter:
        iterations += 1
        f, _, cx, cy, cl = heapq.heappop(open_set)
        ck = (cx, cy, cl)
        ch = _octile_h(cx, cy, gx2, gy2)
        if ch < best_h:
            best_h = ch
            best_cell = (cx, cy, cl)

        if cx == gx2 and cy == gy2 and cl in end_layers:
            goal_key = ck
            break

        cg = g_score.get(ck)
        if cg is None or f > cg + ch + STRAIGHT_COST:
            continue

        for ndx, ndy, cost in NEIGHBOURS:
            nx, ny = cx + ndx, cy + ndy
            if nx < 0 or ny < 0 or nx >= W or ny >= H:
                continue
            is_goal = (nx == gx2 and ny == gy2)
            if not is_goal and blocked[cl][ny, nx]:
                continue
            # For diagonals, also check the two adjacent cells to prevent
            # cutting through blocked corners
            if cost == DIAG_COST and not is_goal:
                if blocked[cl][cy, nx] or blocked[cl][ny, cx]:
                    continue
            ng = cg + cost
            nk = (nx, ny, cl)
            old = g_score.get(nk)
            if old is None or ng < old:
                g_score[nk] = ng
                heapq.heappush(open_set, (ng + _octile_h(nx, ny, gx2, gy2), counter, nx, ny, cl))
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
                        heapq.heappush(open_set, (ng + _octile_h(cx, cy, gx2, gy2), counter, cx, cy, ol))
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
    length_cells_10 = 0  # in units of STRAIGHT_COST (10)
    for i in range(1, len(path)):
        if path[i - 1][2] != path[i][2]:
            via_count += 1
        else:
            dx = abs(path[i][0] - path[i - 1][0])
            dy = abs(path[i][1] - path[i - 1][1])
            if dx and dy:
                length_cells_10 += DIAG_COST
            else:
                length_cells_10 += STRAIGHT_COST

    length_mm = (length_cells_10 / STRAIGHT_COST) * GRID_PITCH
    seg_count = _count_segments_8(path)

    return RouteResult(
        success=True, path=path,
        length_mm=length_mm, via_count=via_count,
        segment_count=seg_count,
        message=f"Routed: {length_mm:.1f}mm, {via_count} vias, {seg_count} segs"
    )


def route8_by_name(grid, net_name, x1_mm, y1_mm, x2_mm, y2_mm,
                   layer_mode='auto', via_cost=300, track_width_cells=2,
                   margin_override=None):
    from dpcb_router import _find_pad_layer
    nid = grid.get_net_id(net_name)
    if not nid:
        return RouteResult(False, message=f"Unknown net: {net_name}")
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    gx2, gy2 = grid.mm_to_grid(x2_mm, y2_mm)
    start_layer = _find_pad_layer(grid, gx1, gy1)
    end_layer = _find_pad_layer(grid, gx2, gy2)
    return route8(grid, x1_mm, y1_mm, x2_mm, y2_mm, nid,
                  layer_mode, via_cost, track_width_cells,
                  start_layer=start_layer, end_layer=end_layer,
                  margin_override=margin_override)


def _flood_same_net_8(grid, net_id, gx, gy, layers):
    """Flood-fill same-net cells — 4-dir is fine here, just finding connected cells."""
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
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
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


def _count_segments_8(path):
    """Count track segments (direction or layer changes)."""
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
