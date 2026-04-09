#!/usr/bin/env python3
"""
grab_layer.py — Capture board state from KiCad via IPC API.

Provides:
    - get_copper_grids() - F.Cu and B.Cu as numpy arrays
    - get_pads() - pad info with ref, pin, net, position
    - get_tracks() - track segments
    - get_vias() - vias
    - get_nets() - net names
    - get_footprints() - component positions
    - capture_board() - all of the above in one call

Usage:
    from grab_layer import capture_board, find_socket

    socket = find_socket()
    data = capture_board(socket)

    # data contains: pads, tracks, vias, nets, footprints, grids, bounds
"""

import glob
import os

import numpy as np
from kipy import KiCad


# Layer constants (KiCad layer IDs)
BL_F_CU = 3
BL_B_CU = 34
# Inner copper layer IDs: In1.Cu=4, In2.Cu=5, ...
# KiCad uses 3=F.Cu, 4=In1.Cu, 5=In2.Cu, ..., 34=B.Cu

# Known KiCad copper layer IDs in stack order
_KICAD_COPPER_IDS = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                     17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
                     30, 31, 32, 33, 34]


def get_copper_layers(board):
    """Detect all copper layers from the board.

    Returns:
        List of (kicad_layer_id, layer_name) in stack order (F.Cu first, B.Cu last).
    """
    try:
        enabled = set(board.get_enabled_layers())
    except (AttributeError, Exception):
        # Fallback: use copper_layer_count to infer layers
        try:
            n = board.get_copper_layer_count()
        except Exception:
            n = 2
        enabled = {BL_F_CU, BL_B_CU}
        for i in range(1, n - 1):
            enabled.add(BL_F_CU + i)  # In1.Cu=4, In2.Cu=5, ...

    copper = []
    for lid in _KICAD_COPPER_IDS:
        if lid in enabled:
            try:
                name = board.get_layer_name(lid)
            except Exception:
                name = f"In{lid - 3}.Cu" if lid not in (BL_F_CU, BL_B_CU) else ("F.Cu" if lid == BL_F_CU else "B.Cu")
            copper.append((lid, name))
    return copper

def find_socket():
    """Find the PCB editor socket in /tmp/kicad/.

    Tries each socket in order of most recently modified, returning the
    first one that successfully connects to KiCad. Falls back to api.sock.
    """
    sockets = glob.glob("/tmp/kicad/api-*.sock")
    if sockets:
        sockets.sort(key=lambda s: os.path.getmtime(s), reverse=True)
        for sock in sockets:
            try:
                from kipy import KiCad
                kicad = KiCad(socket_path=f"ipc://{sock}")
                kicad.get_board()
                return f"ipc://{sock}"
            except Exception:
                continue
    if os.path.exists("/tmp/kicad/api.sock"):
        return "ipc:///tmp/kicad/api.sock"
    return None



def connect(socket_path):
    """Connect to KiCad and return (kicad, board) tuple."""
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()
    return kicad, board


def get_pads(board, copper_layer_ids=None, raw_pads=None):
    """
    Get all pads from the board.

    Args:
        board: KiCad board object
        copper_layer_ids: set of copper layer IDs (auto-detected if None)
        raw_pads: optional pre-fetched list of pad objects. If provided,
            pads are iterated from this list in order, so the output
            list index matches any caller that iterated the same list
            (e.g. get_copper_grids building pad_owner_grids). This is
            how capture_board keeps the pad-dict index aligned with the
            1-based index stored in pad_owner_grids.

    Returns:
        List of dicts: {ref, pin, net, x, y, smd, layers}
    """
    if copper_layer_ids is None:
        copper_layer_ids = {lid for lid, _ in get_copper_layers(board)}

    pads = []

    # Build pad-id → ref map by walking each footprint's own pad list.
    # Pad ownership comes from the KiCad API directly — never infer it by
    # distance to footprint centroid, because edge pads of a wide footprint
    # (e.g. Ra-01 LoRa module, 16 pads spanning 16mm) can sit closer to a
    # neighbouring component's origin than to their own.
    pad_ref_by_id = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if not ref:
            continue
        for fp_pad in fp.definition.pads:
            pad_ref_by_id[fp_pad.id.value] = ref

    pad_source = raw_pads if raw_pads is not None else board.get_pads()
    for pad in pad_source:
        x = pad.position.x / 1000000
        y = pad.position.y / 1000000
        net = pad.net.name if pad.net else ""
        pin = pad.number

        # Get layers
        layers = pad.padstack.layers
        # Count how many copper layers this pad is on
        pad_copper = [lid for lid in layers if lid in copper_layer_ids]

        # SMD if on exactly one copper layer
        smd = (len(pad_copper) == 1)

        ref = pad_ref_by_id.get(pad.id.value, "")

        pads.append({
            "ref": ref,
            "pin": pin,
            "net": net,
            "x": round(x, 3),
            "y": round(y, 3),
            "smd": smd,
            "layers": list(layers)  # convert protobuf to plain list
        })

    return pads


def get_tracks(board, layer_name_map=None):
    """
    Get all tracks from the board.

    Args:
        board: KiCad board object
        layer_name_map: dict {kicad_layer_id: name} (auto-detected if None)

    Returns:
        List of dicts: {x1, y1, x2, y2, width, layer, net}
    """
    if layer_name_map is None:
        layer_name_map = {lid: name for lid, name in get_copper_layers(board)}

    tracks = []

    for track in board.get_tracks():
        x1 = track.start.x / 1000000
        y1 = track.start.y / 1000000
        x2 = track.end.x / 1000000
        y2 = track.end.y / 1000000
        width = track.width / 1000000
        layer = layer_name_map.get(track.layer, f"L{track.layer}")
        net = track.net.name if track.net else ""

        tracks.append({
            "x1": round(x1, 3),
            "y1": round(y1, 3),
            "x2": round(x2, 3),
            "y2": round(y2, 3),
            "width": round(width, 3),
            "layer": layer,
            "net": net
        })

    return tracks


def get_vias(board):
    """
    Get all vias from the board.

    Returns:
        List of dicts: {x, y, od, id, net}
    """
    vias = []

    for via in board.get_vias():
        x = via.position.x / 1000000
        y = via.position.y / 1000000
        # Via size - use diameter and drill_diameter properties
        od = 0.6  # default outer diameter
        drill_id = 0.3  # default drill
        try:
            # Primary: via.diameter and via.drill_diameter (in nm)
            if hasattr(via, 'diameter') and via.diameter:
                od = via.diameter / 1000000
            if hasattr(via, 'drill_diameter') and via.drill_diameter:
                drill_id = via.drill_diameter / 1000000
        except (AttributeError, TypeError):
            pass
        net = via.net.name if via.net else ""

        vias.append({
            "x": round(x, 3),
            "y": round(y, 3),
            "od": round(od, 3),
            "id": round(drill_id, 3),
            "net": net
        })

    return vias


def get_nets(board):
    """
    Get all net names from the board.

    Returns:
        List of net names (strings)
    """
    return [net.name for net in board.get_nets() if net.name]


def get_design_rules(board):
    """
    Get design rules from KiCad board.

    Returns:
        Dict with clearance, track_width, via_diameter, via_drill (all in mm)
    """
    # Get a net to query the default netclass
    nets = list(board.get_nets())
    if not nets:
        # Return defaults if no nets
        return {
            "clearance": 0.2,
            "track_width": 0.2,
            "via_diameter": 0.6,
            "via_drill": 0.3,
        }

    # Find a net with a name (skip empty net)
    net_obj = None
    for n in nets:
        if n.name:
            net_obj = n
            break

    if not net_obj:
        return {
            "clearance": 0.2,
            "track_width": 0.2,
            "via_diameter": 0.6,
            "via_drill": 0.3,
        }

    # Query netclass for this net
    nc_map = board.get_netclass_for_nets([net_obj])
    nc = nc_map.get(net_obj.name)

    if not nc:
        return {
            "clearance": 0.2,
            "track_width": 0.2,
            "via_diameter": 0.6,
            "via_drill": 0.3,
        }

    # Convert from nanometers to mm
    return {
        "clearance": nc.clearance / 1_000_000 if nc.clearance else 0.2,
        "track_width": nc.track_width / 1_000_000 if nc.track_width else 0.2,
        "via_diameter": nc.via_diameter / 1_000_000 if nc.via_diameter else 0.6,
        "via_drill": nc.via_drill / 1_000_000 if nc.via_drill else 0.3,
    }


def get_footprints(board):
    """
    Get all footprint positions.

    Returns:
        Dict: {ref: {"x": x, "y": y, "rotation": rot}}
    """
    footprints = {}

    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if not ref:
            continue

        x = fp.position.x / 1000000
        y = fp.position.y / 1000000
        rotation = fp.orientation.degrees if hasattr(fp.orientation, 'degrees') else 0

        footprints[ref] = {
            "x": round(x, 3),
            "y": round(y, 3),
            "rotation": rotation
        }

    return footprints


def get_board_bounds(board, margin=5.0):
    """
    Get board bounds from footprint positions.

    Args:
        board: KiCad board object
        margin: Margin to add around footprints (mm)

    Returns:
        (min_x, min_y, max_x, max_y) in mm
    """
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for fp in board.get_footprints():
        x = fp.position.x / 1000000
        y = fp.position.y / 1000000
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)

    return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)


def get_copper_grids(board, pitch_mm=0.1, bounds=None, copper_layers=None, raw_pads=None):
    """
    Get copper layer grids as numpy arrays with actual copper shapes.

    Returns two parallel per-layer grids:
      - copper_grids[layer]   : int8 array, 0/1/2/3 (pad / track / via)
      - pad_owner_grids[layer]: int32 array, 0 = not a pad pixel, N = this
        pixel belongs to the pad at index (N-1) in the enumerated pad list
        (the `raw_pads` arg, or `board.get_pads()` if not provided).

    The owner grid preserves per-pad ownership at rasterisation time,
    which is the information the downstream router-grid builder needs to
    label cells with the correct net id without heuristic scan windows.

    Args:
        board: KiCad board object
        pitch_mm: Grid resolution in mm (default 0.1mm)
        bounds: Optional (min_x, min_y, max_x, max_y) in mm
        copper_layers: list of (kicad_id, name) tuples (auto-detected if None)

    Returns:
        grids: dict {layer_name: numpy array}
        bounds_dict: dict with min_x, max_x, min_y, max_y, pitch_mm, width, height

    Grid values:
        0 = empty
        1 = pad copper
        2 = track copper
        3 = via copper
    """
    if copper_layers is None:
        copper_layers = get_copper_layers(board)

    if bounds is None:
        bounds = get_board_bounds(board)

    min_x, min_y, max_x, max_y = bounds
    width = int((max_x - min_x) / pitch_mm) + 1
    height = int((max_y - min_y) / pitch_mm) + 1

    # Build per-layer grids
    grids = {}
    pad_owner_grids = {}
    lid_to_name = {}
    for lid, name in copper_layers:
        grids[name] = np.zeros((height, width), dtype=np.int8)
        pad_owner_grids[name] = np.zeros((height, width), dtype=np.int32)
        lid_to_name[lid] = name

    def mm_to_grid(x_mm, y_mm):
        return int((x_mm - min_x) / pitch_mm), int((y_mm - min_y) / pitch_mm)

    def fill_cell(grid, gx, gy, value):
        if 0 <= gx < width and 0 <= gy < height:
            grid[gy, gx] = max(grid[gy, gx], value)

    def fill_polygon(grid, poly, value):
        """Rasterize a polygon onto the grid using scanline fill."""
        points = []
        for node in poly.outline.nodes:
            x_mm = node.point.x / 1e6
            y_mm = node.point.y / 1e6
            gx, gy = mm_to_grid(x_mm, y_mm)
            points.append((gx, gy))

        if not points:
            return

        min_gy = min(p[1] for p in points)
        max_gy = max(p[1] for p in points)

        for gy in range(max(0, min_gy), min(height, max_gy + 1)):
            intersections = []
            n = len(points)
            for i in range(n):
                x1, y1 = points[i]
                x2, y2 = points[(i + 1) % n]
                if y1 == y2:
                    continue
                if min(y1, y2) <= gy < max(y1, y2):
                    x = x1 + (gy - y1) * (x2 - x1) / (y2 - y1)
                    intersections.append(int(x))

            intersections.sort()
            for i in range(0, len(intersections) - 1, 2):
                for gx in range(max(0, intersections[i]), min(width, intersections[i + 1] + 1)):
                    fill_cell(grid, gx, gy, value)

    def fill_polygon_owner(grid, poly, pad_index):
        """Rasterize a polygon into an owner grid with write-if-empty
        semantics. Each interior cell is tagged with `pad_index` only if
        it is still 0 — so if two pads' polygons touch (they should not on
        a valid board, but defensively) the first writer wins rather than
        the last. Parallel structure to fill_polygon."""
        points = []
        for node in poly.outline.nodes:
            x_mm = node.point.x / 1e6
            y_mm = node.point.y / 1e6
            gx, gy = mm_to_grid(x_mm, y_mm)
            points.append((gx, gy))

        if not points:
            return

        min_gy = min(p[1] for p in points)
        max_gy = max(p[1] for p in points)

        for gy in range(max(0, min_gy), min(height, max_gy + 1)):
            intersections = []
            n = len(points)
            for i in range(n):
                x1, y1 = points[i]
                x2, y2 = points[(i + 1) % n]
                if y1 == y2:
                    continue
                if min(y1, y2) <= gy < max(y1, y2):
                    x = x1 + (gy - y1) * (x2 - x1) / (y2 - y1)
                    intersections.append(int(x))

            intersections.sort()
            for i in range(0, len(intersections) - 1, 2):
                for gx in range(max(0, intersections[i]), min(width, intersections[i + 1] + 1)):
                    if 0 <= gx < width and 0 <= gy < height:
                        if grid[gy, gx] == 0:
                            grid[gy, gx] = pad_index

    def fill_thick_line(grid, x1, y1, x2, y2, width_mm, value):
        """Rasterize a track with its actual width."""
        gx1, gy1 = mm_to_grid(x1, y1)
        gx2, gy2 = mm_to_grid(x2, y2)
        # Half-width in grid cells — use ceil to avoid undercount,
        # but check actual distance to avoid overcount
        hw_mm = width_mm / 2
        hw = int(hw_mm / pitch_mm + 0.5)  # round
        hw = max(0, hw)
        hw_sq = (hw_mm / pitch_mm) ** 2  # for distance check

        steps = max(abs(gx2 - gx1), abs(gy2 - gy1), 1)
        for i in range(steps + 1):
            t = i / steps
            gx = int(gx1 + t * (gx2 - gx1))
            gy = int(gy1 + t * (gy2 - gy1))
            for dy in range(-hw, hw + 1):
                for dx in range(-hw, hw + 1):
                    if dx * dx + dy * dy <= hw_sq:
                        fill_cell(grid, gx + dx, gy + dy, value)

    def fill_circle(grid, cx, cy, radius_mm, value):
        """Rasterize a circular via/pad."""
        gcx, gcy = mm_to_grid(cx, cy)
        gr = int(radius_mm / pitch_mm) + 1

        for dy in range(-gr, gr + 1):
            for dx in range(-gr, gr + 1):
                if dx * dx + dy * dy <= gr * gr:
                    fill_cell(grid, gcx + dx, gcy + dy, value)

    # Rasterize pads with actual shapes.
    # Optimization: get polygon once per pad and stamp on all its layers.
    # Through-hole pads have the same shape on all copper layers.
    #
    # Iterate `raw_pads` if provided so the 1-based index stored in
    # pad_owner_grids matches the caller's pad-dict list index (see the
    # docstring). Each pad writes into copper_grids (value=1, for the
    # visual heatmap) AND into pad_owner_grids (value=pad_index, for the
    # downstream router-grid net labelling).
    pads_iter = raw_pads if raw_pads is not None else list(board.get_pads())
    for pad_index, pad in enumerate(pads_iter, start=1):
        pad_layers = pad.padstack.layers
        pad_copper = [lid for lid in pad_layers if lid in lid_to_name]
        if not pad_copper:
            continue
        # Get polygon from the first copper layer this pad is on
        poly = board.get_pad_shapes_as_polygons(pad, layer=pad_copper[0])
        if not poly:
            continue
        for lid in pad_copper:
            layer_name = lid_to_name[lid]
            fill_polygon(grids[layer_name], poly, 1)
            fill_polygon_owner(pad_owner_grids[layer_name], poly, pad_index)

    # Tracks and vias are not rasterized into the heatmap — the grid
    # resolution (0.1mm) makes them appear fatter than reality.
    # The viewer draws tracks/vias accurately from vector data.
    # Only pads use the rasterized grid (polygon shapes from KiCad).

    # Rasterize vias as pad-like objects (through-hole: all layers)
    for via in board.get_vias():
        x = via.position.x / 1e6
        y = via.position.y / 1e6
        od = 0.6  # default
        try:
            if via.padstack.copper_layers:
                size = via.padstack.copper_layers[0].size
                if hasattr(size, 'x'):
                    od = size.x / 1e6
                else:
                    od = size / 1e6
        except (AttributeError, TypeError):
            pass
        radius = od / 2
        for name in grids:
            fill_circle(grids[name], x, y, radius, 3)

    bounds_dict = {
        'min_x': min_x, 'max_x': max_x,
        'min_y': min_y, 'max_y': max_y,
        'pitch_mm': pitch_mm,
        'width': width, 'height': height
    }

    return grids, pad_owner_grids, bounds_dict


def capture_board(socket_path, pitch_mm=0.1):
    """
    Capture complete board state from KiCad.

    Args:
        socket_path: KiCad IPC socket (e.g. "ipc:///tmp/kicad/api-41011.sock")
        pitch_mm: Grid resolution in mm (default 0.1mm)

    Returns:
        dict with:
            pads: list of pad dicts
            tracks: list of track dicts
            vias: list of via dicts
            nets: list of net names
            footprints: dict of ref -> position
            fcu: numpy array for F.Cu
            bcu: numpy array for B.Cu
            bounds: bounds dict
    """
    import time as _time
    _t0 = _time.perf_counter()

    kicad, board = connect(socket_path)
    print(f"  connect: {_time.perf_counter()-_t0:.1f}s")

    copper_layers = get_copper_layers(board)
    copper_layer_ids = {lid for lid, _ in copper_layers}
    layer_name_map = {lid: name for lid, name in copper_layers}
    print(f"  get_copper_layers: {_time.perf_counter()-_t0:.1f}s")

    bounds = get_board_bounds(board)
    print(f"  bounds: {_time.perf_counter()-_t0:.1f}s")

    # Fetch pads once and share the list with both helpers, so the
    # 1-based pad index written into pad_owner_grids by get_copper_grids
    # matches the list-index of the same pad in _pads.
    _raw_pads = list(board.get_pads())
    copper_grids, pad_owner_grids, bounds_dict = get_copper_grids(
        board, pitch_mm, bounds, copper_layers, raw_pads=_raw_pads)
    print(f"  copper_grids: {_time.perf_counter()-_t0:.1f}s")

    _pads = get_pads(board, copper_layer_ids, raw_pads=_raw_pads)
    print(f"  get_pads: {_time.perf_counter()-_t0:.1f}s")
    _tracks = get_tracks(board, layer_name_map)
    print(f"  get_tracks: {_time.perf_counter()-_t0:.1f}s")
    _vias = get_vias(board)
    print(f"  get_vias: {_time.perf_counter()-_t0:.1f}s")
    _nets = get_nets(board)
    _footprints = get_footprints(board)
    _rules = get_design_rules(board)
    print(f"  nets/fps/rules: {_time.perf_counter()-_t0:.1f}s")

    result = {
        "pads": _pads,
        "tracks": _tracks,
        "vias": _vias,
        "nets": _nets,
        "footprints": _footprints,
        "rules": _rules,
        "copper_grids": copper_grids,
        "pad_owner_grids": pad_owner_grids,
        "copper_layers": [name for _, name in copper_layers],
        "bounds": bounds_dict,
        "board_filename": os.path.join(board.get_project().path, board.name),
    }
    # Backward compat aliases
    if "F.Cu" in copper_grids:
        result["fcu"] = copper_grids["F.Cu"]
    if "B.Cu" in copper_grids:
        result["bcu"] = copper_grids["B.Cu"]
    return result


def print_grid(grid, legend=None):
    """Print grid as ASCII art."""
    if legend is None:
        legend = {0: '.', 1: '#', 2: '=', 3: 'O'}

    for y in range(grid.shape[0]):
        row = ""
        for x in range(grid.shape[1]):
            row += legend.get(grid[y, x], '?')
        print(row)


def grid_to_png_base64(copper_grids_or_fcu, bcu_or_bounds=None, bounds_dict=None):
    """Convert copper grids to a PNG image as base64 data URL.

    Supports both old 2-arg style (fcu, bcu, bounds) and new dict style (copper_grids, bounds).

    Args:
        copper_grids_or_fcu: dict {layer_name: array} or numpy array for F.Cu
        bcu_or_bounds: numpy array for B.Cu (old style) or bounds_dict (new style)
        bounds_dict: bounds dict (old style only)

    Returns:
        Base64 data URL string for the PNG image
    """
    try:
        from PIL import Image
        import base64
        import io
    except ImportError:
        return None

    # Handle both old and new calling conventions
    if isinstance(copper_grids_or_fcu, dict):
        copper_grids = copper_grids_or_fcu
        bounds_dict = bcu_or_bounds
    else:
        # Old style: (fcu, bcu, bounds)
        copper_grids = {"F.Cu": copper_grids_or_fcu}
        if bcu_or_bounds is not None and not isinstance(bcu_or_bounds, dict):
            copper_grids["B.Cu"] = bcu_or_bounds

    if not copper_grids:
        return None

    first_grid = next(iter(copper_grids.values()))
    height, width = first_grid.shape

    # Layer colors: F.Cu=red, In1=yellow, In2=green, ..., B.Cu=blue
    LAYER_COLORS = {
        "F.Cu": (255, 80, 80, 140),
        "B.Cu": (80, 80, 255, 140),
    }
    INNER_COLORS = [
        (220, 180, 40, 140),   # yellow
        (40, 180, 80, 140),    # green
        (180, 40, 180, 140),   # magenta
        (40, 180, 180, 140),   # cyan
    ]

    # Assign colors to layers
    layer_names = list(copper_grids.keys())
    layer_colors = {}
    inner_idx = 0
    for name in layer_names:
        if name in LAYER_COLORS:
            layer_colors[name] = LAYER_COLORS[name]
        else:
            layer_colors[name] = INNER_COLORS[inner_idx % len(INNER_COLORS)]
            inner_idx += 1

    # Create RGBA image
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pixels = img.load()

    for y in range(height):
        for x in range(width):
            active = [name for name in layer_names if copper_grids[name][y, x] > 0]
            if not active:
                continue
            if len(active) > 1:
                # Multiple layers: blend colors
                r = sum(layer_colors[n][0] for n in active) // len(active)
                g = sum(layer_colors[n][1] for n in active) // len(active)
                b = sum(layer_colors[n][2] for n in active) // len(active)
                pixels[x, y] = (r, g, b, 160)
            else:
                pixels[x, y] = layer_colors[active[0]]

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    b64 = base64.b64encode(buffer.read()).decode('ascii')

    return f"data:image/png;base64,{b64}"


if __name__ == "__main__":
    socket = find_socket()
    if not socket:
        print("No KiCad socket found in /tmp/kicad/")
        print("Make sure KiCad PCB editor is running with IPC API enabled.")
        exit(1)

    print(f"Using socket: {socket}")

    data = capture_board(socket, pitch_mm=0.5)

    print(f"\nBoard captured:")
    print(f"  Pads: {len(data['pads'])}")
    print(f"  Tracks: {len(data['tracks'])}")
    print(f"  Vias: {len(data['vias'])}")
    print(f"  Nets: {len(data['nets'])}")
    print(f"  Footprints: {len(data['footprints'])}")
    print(f"  Copper layers: {data['copper_layers']}")
    print(f"  Grid: {data['bounds']['width']}x{data['bounds']['height']} at {data['bounds']['pitch_mm']}mm")
    print(f"  Bounds: ({data['bounds']['min_x']:.1f}, {data['bounds']['min_y']:.1f}) to ({data['bounds']['max_x']:.1f}, {data['bounds']['max_y']:.1f}) mm")
    for name, grid in data['copper_grids'].items():
        print(f"  {name} cells: {np.count_nonzero(grid)}")

    print("\n=== Sample pads ===")
    for p in data['pads'][:5]:
        print(f"  {p['ref']}.{p['pin']}: ({p['x']}, {p['y']}) net={p['net']}")

    if "fcu" in data:
        print("\n=== F.Cu (0.5mm pitch) ===")
        print("Legend: . = empty, # = pad, = = track, O = via")
        print_grid(data['fcu'])
