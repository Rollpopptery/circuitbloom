#!/usr/bin/env python3
"""
dpcb_router_grid.py — RouterGrid with separate pad and route layers.

Two separate grids:
    _pad_grid   — permanent pad copper + clearance zones, set at capture,
                  never modified by routing.
    _route_grid — routing state (traces, vias), modified during routing.
                  clear_track() only touches _route_grid.
    _combined   — pre-computed combined view, kept up to date incrementally.
                  pad_grid takes priority. Reading occupy[] is free.

All callers use RouterGrid methods — never access internal arrays directly.
"""

import numpy as np


GRID_PITCH = 0.1  # mm per grid cell

LAYER_FCU = 0
LAYER_BCU = 1
LAYER_NAMES = {0: 'F.Cu', 1: 'B.Cu'}
LAYER_IDS = {'F.Cu': 0, 'B.Cu': 1}


def make_layer_maps(num_layers, custom_names=None):
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

def _line_cells_fast(x1, y1, x2, y2):
    """Bresenham line — visits minimum cells. Use for marking/clearing tracks."""
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


def _line_cells(x1, y1, x2, y2):
    """Return all grid cells the line segment passes through (supercover DDA)."""
    cells = []
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    sx = 1 if x2 > x1 else -1
    sy = 1 if y2 > y1 else -1
    x, y = x1, y1
    cells.append((x, y))
    if dx == 0 and dy == 0:
        return cells
    if dx == 0:
        for _ in range(dy):
            y += sy
            cells.append((x, y))
        return cells
    if dy == 0:
        for _ in range(dx):
            x += sx
            cells.append((x, y))
        return cells
    t_max_x = dy
    t_max_y = dx
    t_delta_x = 2 * dy
    t_delta_y = 2 * dx
    steps = dx + dy
    for _ in range(steps):
        if t_max_x < t_max_y:
            x += sx
            t_max_x += t_delta_x
        elif t_max_x > t_max_y:
            y += sy
            t_max_y += t_delta_y
        else:
            x += sx
            y += sy
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        cells.append((x, y))
    return cells


class RouterGrid:
    """
    N-layer PCB routing grid with separate pad and route layers.

    Internal arrays (never access directly from outside):
        _pad_grid[layer][y, x]   — permanent pad copper + clearance zones
        _route_grid[layer][y, x] — routing state (traces, vias)
        _combined[layer][y, x]   — pre-computed combined view (read-only)

    _combined is kept up to date by every write method so that
    grid.occupy[layer] is a free array reference, not a computation.
    pad_grid takes priority: if a cell has pad copper, _combined shows
    that regardless of what route_grid has.
    """

    def __init__(self, width_mm, height_mm, clearance_cells, via_od_cells, via_id_cells,
                 num_layers=2, layer_names=None):
        self.width = int(round(width_mm / GRID_PITCH))
        self.height = int(round(height_mm / GRID_PITCH))
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.clearance = clearance_cells
        self.via_od = via_od_cells
        self.via_id = via_id_cells
        self.num_layers = num_layers

        if layer_names:
            self.layer_names = dict(layer_names)
            self.layer_ids = {v: k for k, v in self.layer_names.items()}
        else:
            self.layer_names, self.layer_ids = make_layer_maps(num_layers)

        self._pad_grid = [
            np.zeros((self.height, self.width), dtype=np.int32)
            for _ in range(num_layers)
        ]
        self._route_grid = [
            np.zeros((self.height, self.width), dtype=np.int32)
            for _ in range(num_layers)
        ]
        # Pre-computed combined view — updated incrementally on every write
        self._combined = [
            np.zeros((self.height, self.width), dtype=np.int32)
            for _ in range(num_layers)
        ]

        self.net_ids = {}
        self.pad_layers = {}
        self.pad_keepout = set()
        self.pad_cells = {}

    # ============================================================
    # COMBINED VIEW — free array reference, no computation
    # ============================================================

    @property
    def occupy(self):
        """Pre-computed combined view. pad_grid takes priority over route_grid.

        Returns list of numpy arrays. Reading is free — no computation.
        Do not write to these arrays directly.
        """
        return self._combined

    def _update_combined(self, layer, x, y):
        """Update _combined at one cell after a write to pad or route grid."""
        pad = int(self._pad_grid[layer][y, x])
        self._combined[layer][y, x] = pad if pad != 0 else int(self._route_grid[layer][y, x])

    # ============================================================
    # CELL ACCESS METHODS
    # ============================================================

    def get_cell(self, layer, x, y):
        """Return effective occupant at (layer, x, y)."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        return int(self._combined[layer][y, x])

    def is_foreign(self, layer, x, y, nid):
        """Return True if cell is occupied by a net other than nid."""
        cell = self.get_cell(layer, x, y)
        return cell != 0 and cell != nid

    def set_pad(self, layer, x, y, nid):
        """Write to pad_grid (permanent). Updates _combined."""
        if 0 <= x < self.width and 0 <= y < self.height:
            self._pad_grid[layer][y, x] = nid
            self._combined[layer][y, x] = nid  # pad takes priority always

    def set_track(self, layer, x, y, nid):
        """Write to route_grid only if cell is empty or same net. Updates _combined."""
        if 0 <= x < self.width and 0 <= y < self.height:
            existing = int(self._combined[layer][y, x])
            if existing == 0 or existing == nid:
                self._route_grid[layer][y, x] = nid
                if self._pad_grid[layer][y, x] == 0:
                    self._combined[layer][y, x] = nid

    def clear_track(self, layer, x, y, nid):
        """Clear from route_grid only — never touches pad_grid. Updates _combined."""
        if 0 <= x < self.width and 0 <= y < self.height:
            if self._route_grid[layer][y, x] == nid:
                self._route_grid[layer][y, x] = 0
                # Only update combined if pad_grid doesn't own this cell
                if self._pad_grid[layer][y, x] == 0:
                    self._combined[layer][y, x] = 0

    # ============================================================
    # LEGACY METHODS
    # ============================================================

    def set_cell(self, layer, x, y, net_id):
        """Legacy: write to route_grid."""
        if 0 <= x < self.width and 0 <= y < self.height:
            self._route_grid[layer][y, x] = net_id
            if self._pad_grid[layer][y, x] == 0:
                self._combined[layer][y, x] = net_id

    def clear_cell(self, layer, x, y, net_id):
        """Legacy: clear from route_grid only."""
        self.clear_track(layer, x, y, net_id)

    # ============================================================
    # COORDINATE HELPERS
    # ============================================================

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def mm_to_grid(self, mm_x, mm_y):
        return int(round(mm_x / GRID_PITCH)), int(round(mm_y / GRID_PITCH))

    def grid_to_mm(self, gx, gy):
        return round(gx * GRID_PITCH, 4), round(gy * GRID_PITCH, 4)

    # ============================================================
    # MARK METHODS
    # ============================================================

    def mark_line(self, layer, x1, y1, x2, y2, net_id, half_width=0):
        """Mark a line of cells in route_grid."""
        cells = _line_cells_fast(x1, y1, x2, y2)
        for cx, cy in cells:
            for dy in range(-half_width, half_width + 1):
                for dx in range(-half_width, half_width + 1):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        existing = int(self._combined[layer][ny, nx])
                        if existing == 0 or existing == net_id:
                            self._route_grid[layer][ny, nx] = net_id
                            if self._pad_grid[layer][ny, nx] == 0:
                                self._combined[layer][ny, nx] = net_id

    def mark_circle(self, layer, cx, cy, radius, net_id):
        """Mark a circle in route_grid."""
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        self._route_grid[layer][ny, nx] = net_id
                        if self._pad_grid[layer][ny, nx] == 0:
                            self._combined[layer][ny, nx] = net_id

    def mark_pad_circle(self, layer, cx, cy, radius, net_id):
        """Mark a circle in pad_grid (permanent pad copper)."""
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        self._pad_grid[layer][ny, nx] = net_id
                        self._combined[layer][ny, nx] = net_id  # pad always wins

    def mark_track(self, x1_mm, y1_mm, x2_mm, y2_mm, width_cells, layer, net_id):
        gx1, gy1 = self.mm_to_grid(x1_mm, y1_mm)
        gx2, gy2 = self.mm_to_grid(x2_mm, y2_mm)
        self.mark_line(layer, gx1, gy1, gx2, gy2, net_id, width_cells // 2)

    def mark_via(self, x_mm, y_mm, net_id):
        gx, gy = self.mm_to_grid(x_mm, y_mm)
        for layer in range(self.num_layers):
            self.mark_circle(layer, gx, gy, self.via_od // 2, net_id)

    def mark_pad(self, x_mm, y_mm, radius_cells, layer, net_id):
        """Mark pad copper in pad_grid (permanent)."""
        gx, gy = self.mm_to_grid(x_mm, y_mm)
        self.mark_pad_circle(layer, gx, gy, radius_cells, net_id)
        pad_set = self.pad_cells.setdefault(layer, {}).setdefault(net_id, set())
        r = radius_cells
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        pad_set.add((nx, ny))

    def mark_pad_clearance(self, layer, x, y, nid, clearance_cells):
        """Mark clearance zone around a pad cell in pad_grid.
        Only marks empty cells — existing pad copper is never overwritten.
        """
        for dy in range(-clearance_cells, clearance_cells + 1):
            for dx in range(-clearance_cells, clearance_cells + 1):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                if self._pad_grid[layer][ny, nx] == 0:
                    self._pad_grid[layer][ny, nx] = nid
                    self._combined[layer][ny, nx] = nid

    # ============================================================
    # A* BLOCKED GRID
    # ============================================================

    def build_blocked_grid(self, net_id, margin, keepout_margin=1, track_half_width=0):
        """Build boolean blocked grids for A* using combined view."""
        blocked = [None] * self.num_layers
        foreign_dilation = margin + track_half_width
        keepout_dilation = keepout_margin + track_half_width

        for layer in range(self.num_layers):
            occ = self._combined[layer]  # fast — pre-computed

            real = (occ != 0) & (occ != net_id) & (occ != -1)
            keepout = (occ == -1)

            if foreign_dilation > 0:
                dilated = np.copy(real)
                for dx in range(1, foreign_dilation + 1):
                    dilated[:, dx:] |= real[:, :-dx]
                    dilated[:, :-dx] |= real[:, dx:]
                h_dilated = np.copy(dilated)
                for dy in range(1, foreign_dilation + 1):
                    dilated[dy:, :] |= h_dilated[:-dy, :]
                    dilated[:-dy, :] |= h_dilated[dy:, :]
            else:
                dilated = np.copy(real)

            if keepout_dilation > 0:
                ko_dilated = np.copy(keepout)
                for dx in range(1, keepout_dilation + 1):
                    ko_dilated[:, dx:] |= keepout[:, :-dx]
                    ko_dilated[:, :-dx] |= keepout[:, dx:]
                ko_h = np.copy(ko_dilated)
                for dy in range(1, keepout_dilation + 1):
                    ko_dilated[dy:, :] |= ko_h[:-dy, :]
                    ko_dilated[:-dy, :] |= ko_h[dy:, :]
                dilated |= ko_dilated
            else:
                dilated |= keepout

            for (px, py), pad_layer in self.pad_layers.items():
                if pad_layer is not None and pad_layer != layer:
                    continue
                if 0 <= px < self.width and 0 <= py < self.height:
                    if occ[py, px] == net_id:
                        dilated[py, px] = False

            blocked[layer] = dilated
        return blocked

    # ============================================================
    # POPULATE FROM BOARD (legacy .dpcb path)
    # ============================================================

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

        pad_r = 4
        for fp in board.footprints:
            for pad in fp.abs_pads:
                is_th = getattr(pad, 'pad_type', 'th') == 'th'
                pad_layer = None if is_th else 0
                nid = pad_net.get((fp.ref, pad.num), 0)
                if nid == 0:
                    nid = -1
                gx, gy = self.mm_to_grid(pad.x, pad.y)
                self.pad_layers[(gx, gy)] = pad_layer
                via_keepout_r = pad_r + 2
                for dy in range(-via_keepout_r, via_keepout_r + 1):
                    for dx in range(-via_keepout_r, via_keepout_r + 1):
                        if dx * dx + dy * dy <= via_keepout_r * via_keepout_r:
                            self.pad_keepout.add((gx + dx, gy + dy))
                layers_to_mark = list(range(self.num_layers)) if is_th else [0]
                for layer in layers_to_mark:
                    self.mark_pad(pad.x, pad.y, pad_r, layer, nid)

    # ============================================================
    # HELPERS
    # ============================================================

    def get_net_id(self, net_name):
        return self.net_ids.get(net_name, 0)

    def stats(self):
        total = self.width * self.height
        result = {
            'grid_size': f"{self.width}x{self.height}",
            'total_cells': total * self.num_layers,
            'num_layers': self.num_layers,
        }
        for i in range(self.num_layers):
            name = self.layer_names.get(i, f'L{i}')
            safe_name = name.replace('.', '_').lower()
            count = int(np.count_nonzero(self._combined[i]))
            result[f'occupied_{safe_name}'] = count
            result[f'pct_{safe_name}'] = f"{count/total*100:.1f}%"
            result[f'pad_{safe_name}'] = int(np.count_nonzero(self._pad_grid[i]))
            result[f'route_{safe_name}'] = int(np.count_nonzero(self._route_grid[i]))
        return result