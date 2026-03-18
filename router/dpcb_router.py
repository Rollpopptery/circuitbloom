#!/usr/bin/env python3
"""
dpcb_router.py — Grid-based A* PCB router for .dpcb boards.

Operates on a 0.1mm pitch integer grid. Two layers (F.Cu=0, B.Cu=1).

Key optimisation: obstacles are pre-dilated by (track_half_width + clearance)
so A* only checks single cells, not track width at every step.

Usage:
    from dpcb_router import RouterGrid, route, route_by_name
"""

import heapq
import numpy as np
from dataclasses import dataclass, field


GRID_PITCH = 0.1  # mm per grid cell

LAYER_FCU = 0
LAYER_BCU = 1
LAYER_NAMES = {0: 'F.Cu', 1: 'B.Cu'}
LAYER_IDS = {'F.Cu': 0, 'B.Cu': 1}


@dataclass
class RouteResult:
    success: bool
    path: list = field(default_factory=list)
    length_mm: float = 0.0
    via_count: int = 0
    segment_count: int = 0
    message: str = ""

class RouterGrid:
    """
    2-layer occupancy grid using numpy int32 arrays.
    occupy[layer][y, x]: 0=empty, >0=net_id, -1=obstacle
    """

    def __init__(self, width_mm, height_mm, clearance_cells, via_od_cells, via_id_cells):
        self.width = int(round(width_mm / GRID_PITCH))
        self.height = int(round(height_mm / GRID_PITCH))
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.clearance = clearance_cells
        self.via_od = via_od_cells
        self.via_id = via_id_cells

        self.occupy = [
            np.zeros((self.height, self.width), dtype=np.int32),
            np.zeros((self.height, self.width), dtype=np.int32)
        ]
        self.net_ids = {}
        self.pad_layers = {}  # (gx, gy) -> layer (0=F.Cu, 1=B.Cu, None=both)
        self.pad_keepout = set()  # (gx, gy) positions where vias are blocked

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def mm_to_grid(self, mm_x, mm_y):
        return int(round(mm_x / GRID_PITCH)), int(round(mm_y / GRID_PITCH))

    def grid_to_mm(self, gx, gy):
        return round(gx * GRID_PITCH, 4), round(gy * GRID_PITCH, 4)

    def set_cell(self, layer, x, y, net_id):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.occupy[layer][y, x] = net_id

    def clear_cell(self, layer, x, y, net_id):
        if 0 <= x < self.width and 0 <= y < self.height:
            if self.occupy[layer][y, x] == net_id:
                self.occupy[layer][y, x] = 0

    def build_blocked_grid(self, net_id, margin, keepout_margin=1):
        """
        Build boolean blocked grids for A*.
        Real obstacles get full clearance dilation.
        Keepout cells (net_id == -1) get minimal dilation.
        """
        blocked = [None, None]
        for layer in (0, 1):
            occ = self.occupy[layer]
            # Real obstacles: foreign cells that aren't keepouts
            real = (occ != 0) & (occ != net_id) & (occ != -1)
            # Keepout cells
            keepout = (occ == -1)

            # Dilate real obstacles with full margin
            if margin > 0:
                dilated = np.copy(real)
                for dx in range(1, margin + 1):
                    dilated[:, dx:] |= real[:, :-dx]
                    dilated[:, :-dx] |= real[:, dx:]
                h_dilated = np.copy(dilated)
                for dy in range(1, margin + 1):
                    dilated[dy:, :] |= h_dilated[:-dy, :]
                    dilated[:-dy, :] |= h_dilated[dy:, :]
            else:
                dilated = np.copy(real)

            # Dilate keepouts with minimal margin
            if keepout_margin > 0:
                ko_dilated = np.copy(keepout)
                for dx in range(1, keepout_margin + 1):
                    ko_dilated[:, dx:] |= keepout[:, :-dx]
                    ko_dilated[:, :-dx] |= keepout[:, dx:]
                ko_h = np.copy(ko_dilated)
                for dy in range(1, keepout_margin + 1):
                    ko_dilated[dy:, :] |= ko_h[:-dy, :]
                    ko_dilated[:-dy, :] |= ko_h[dy:, :]
                dilated |= ko_dilated
            else:
                dilated |= keepout

            blocked[layer] = dilated
        return blocked

    def mark_line(self, layer, x1, y1, x2, y2, net_id, half_width=0):
        cells = _line_cells(x1, y1, x2, y2)
        for cx, cy in cells:
            for dy in range(-half_width, half_width + 1):
                for dx in range(-half_width, half_width + 1):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        self.occupy[layer][ny, nx] = net_id

    def mark_circle(self, layer, cx, cy, radius, net_id):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        self.occupy[layer][ny, nx] = net_id

    def mark_track(self, x1_mm, y1_mm, x2_mm, y2_mm, width_cells, layer, net_id):
        gx1, gy1 = self.mm_to_grid(x1_mm, y1_mm)
        gx2, gy2 = self.mm_to_grid(x2_mm, y2_mm)
        self.mark_line(layer, gx1, gy1, gx2, gy2, net_id, width_cells // 2)

    def mark_via(self, x_mm, y_mm, net_id):
        gx, gy = self.mm_to_grid(x_mm, y_mm)
        for layer in (0, 1):
            self.mark_circle(layer, gx, gy, self.via_od // 2, net_id)

    def mark_pad(self, x_mm, y_mm, radius_cells, layer, net_id):
        gx, gy = self.mm_to_grid(x_mm, y_mm)
        self.mark_circle(layer, gx, gy, radius_cells, net_id)

    def populate_from_board(self, board):
        self.net_ids = {}
        for i, net in enumerate(board.nets):
            self.net_ids[net.name] = i + 1

        pad_net = {}
        for net in board.nets:
            nid = self.net_ids[net.name]
            for ref, pin in net.pads:
                pad_net[(ref, pin)] = nid

        for trk in board.tracks:
            layer = LAYER_IDS.get(trk.layer, 0)
            nid = self.net_ids.get(trk.net, 0)
            w = max(1, int(round(trk.width / GRID_PITCH)))
            self.mark_track(trk.x1, trk.y1, trk.x2, trk.y2, w, layer, nid)

        for via in board.vias:
            nid = self.net_ids.get(via.net, 0)
            self.mark_via(via.x, via.y, nid)

        track_w = max(1, int(round(board.rules.track / GRID_PITCH)))
        # Pad radius: 4 cells (~0.4mm). Must be small enough that
        # pad_r + margin < min_pad_pitch (TSSOP = 6.5 cells).
        pad_r = 4
        for fp in board.footprints:
            # Determine pad layers: SMD = F.Cu only, through-hole = both
            is_smd = ('_SMD' in fp.lib or 'Package_SO' in fp.lib or
                      'SOIC' in fp.footprint or 'TSSOP' in fp.footprint or
                      'QFP' in fp.footprint or 'BGA' in fp.footprint)
            pad_layer = 0 if is_smd else None  # None = both layers (through-hole)

            for pad in fp.abs_pads:
                # Both pad.num and net.pads pin are int
                nid = pad_net.get((fp.ref, pad.num), 0)
                # Pads not in any net (nid=0) should block as obstacles (-1)
                if nid == 0:
                    nid = -1
                # Store pad layer for route endpoint constraints
                gx, gy = self.mm_to_grid(pad.x, pad.y)
                self.pad_layers[(gx, gy)] = pad_layer
                # Add pad keepout zone for vias (block vias near ALL pads)
                via_keepout_r = pad_r + 2  # block vias from landing directly on pads
                for dy in range(-via_keepout_r, via_keepout_r + 1):
                    for dx in range(-via_keepout_r, via_keepout_r + 1):
                        if dx * dx + dy * dy <= via_keepout_r * via_keepout_r:
                            self.pad_keepout.add((gx + dx, gy + dy))
                # Mark pad on appropriate layers
                layers_to_mark = [0] if is_smd else [0, 1]
                for layer in layers_to_mark:
                    self.mark_pad(pad.x, pad.y, pad_r, layer, nid)

    def get_net_id(self, net_name):
        return self.net_ids.get(net_name, 0)

    def stats(self):
        total = self.width * self.height
        occ = [int(np.count_nonzero(self.occupy[l])) for l in (0, 1)]
        return {
            'grid_size': f"{self.width}x{self.height}",
            'total_cells': total * 2,
            'occupied_fcu': occ[0],
            'occupied_bcu': occ[1],
            'pct_fcu': f"{occ[0]/total*100:.1f}%",
            'pct_bcu': f"{occ[1]/total*100:.1f}%",
        }


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
        ol = 1 - cl
        if ol in layers and grid.occupy[ol][cy, cx] == net_id:
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

    if layer_mode == 'F.Cu':
        allowed = {0}
    elif layer_mode == 'B.Cu':
        allowed = {1}
    else:
        allowed = {0, 1}

    # Constrain start/end layers if specified (for SMD pads)
    start_layers = {start_layer} if start_layer is not None else allowed
    end_layers = {end_layer} if end_layer is not None else allowed

    margin = margin_override if margin_override is not None else 1
    blocked = grid.build_blocked_grid(net_id, margin)

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

        # Via - blocked near pads regardless of net
        if len(allowed) > 1:
            ol = 1 - cl
            is_goal = (cx == gx2 and cy == gy2)
            # Only allow via at goal for through-hole pads (end_layer=None)
            goal_via_ok = is_goal and end_layer is None
            via_allowed = not blocked[ol][cy, cx] and (cx, cy) not in grid.pad_keepout
            if goal_via_ok or via_allowed:
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
        bl_name = LAYER_NAMES.get(bl, str(bl))
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


def route_by_name(grid, net_name, x1_mm, y1_mm, x2_mm, y2_mm,
                  layer_mode='auto', via_cost=30, track_width_cells=2,
                  margin_override=None):
    nid = grid.get_net_id(net_name)
    if not nid:
        return RouteResult(False, message=f"Unknown net: {net_name}")
    # Look up pad layers for start/end points
    gx1, gy1 = grid.mm_to_grid(x1_mm, y1_mm)
    gx2, gy2 = grid.mm_to_grid(x2_mm, y2_mm)
    start_layer = grid.pad_layers.get((gx1, gy1))  # None if not a pad or through-hole
    end_layer = grid.pad_layers.get((gx2, gy2))
    return route(grid, x1_mm, y1_mm, x2_mm, y2_mm, nid,
                 layer_mode, via_cost, track_width_cells,
                 start_layer=start_layer, end_layer=end_layer,
                 margin_override=margin_override)


def _line_cells(x1, y1, x2, y2):
    cells = []
    dx, dy = abs(x2-x1), abs(y2-y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx - dy
    while True:
        cells.append((x1, y1))
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x1 += sx
        if e2 < dx:
            err += dx; y1 += sy
    return cells


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