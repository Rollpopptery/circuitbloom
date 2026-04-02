"""BFS-based via placement finder.

Flood-fills from a pad on its layer through passable cells (empty or same-net),
then checks that a via footprint fits on both layers with clearance.
Guarantees the path from pad to via is clear.
"""

from collections import deque
from dpcb_router import GRID_PITCH


def find_via_spot(grid, net, x_mm, y_mm, margin=3, min_radius=10, max_radius=50):
    """Find the nearest reachable spot where a via fits.

    Args:
        grid: RouterGrid instance
        net: net name
        x_mm, y_mm: starting pad position in mm
        margin: clearance cells for via check
        min_radius: minimum BFS distance in grid cells (skip spots closer than this)
        max_radius: max BFS distance in grid cells

    Returns:
        dict with via position and path info, or error
    """
    if not grid:
        return {"ok": False, "error": "no grid loaded"}

    nid = grid.get_net_id(net)
    gx, gy = grid.mm_to_grid(x_mm, y_mm)

    # Determine start layer from pad
    pad_layer = grid.pad_layers.get((gx, gy))
    if pad_layer is None:
        start_layer = 0  # through-hole, default F.Cu
    else:
        start_layer = pad_layer  # SMD layer

    via_r = grid.via_od // 2

    def via_fits(cx, cy):
        """Check via footprint clear on both layers with margin."""
        if (cx, cy) in grid.pad_keepout:
            return False
        check_r = via_r + margin
        for layer in (0, 1):
            for dy in range(-check_r, check_r + 1):
                for dx in range(-check_r, check_r + 1):
                    if dx * dx + dy * dy > check_r * check_r:
                        continue
                    nx, ny = cx + dx, cy + dy
                    if not grid.in_bounds(nx, ny):
                        return False
                    occ = grid.occupy[layer][ny, nx]
                    if occ != 0 and occ != nid:
                        return False
        return True

    # BFS on start_layer, only through passable cells
    visited = set()
    queue = deque()
    queue.append((gx, gy, 0))
    visited.add((gx, gy))

    while queue:
        cx, cy, dist = queue.popleft()
        if dist > max_radius:
            break

        # Check if via fits here (only beyond min_radius)
        if dist >= min_radius and via_fits(cx, cy):
            vx_mm, vy_mm = grid.grid_to_mm(cx, cy)
            return {
                "ok": True,
                "x": vx_mm,
                "y": vy_mm,
                "distance_mm": round(dist * GRID_PITCH, 2),
                "layer": "F.Cu" if start_layer == 0 else "B.Cu"
            }

        # Expand to 4-connected neighbors on the start layer
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in visited:
                continue
            if not grid.in_bounds(nx, ny):
                continue
            occ = grid.occupy[start_layer][ny, nx]
            if occ == 0 or occ == nid:
                visited.add((nx, ny))
                queue.append((nx, ny, dist + 1))

    return {"ok": False, "error": "no clear via spot found within search radius"}
