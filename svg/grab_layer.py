#!/usr/bin/env python3
"""
grab_layer.py — Capture board state from KiCad via IPC API.

Provides:
    - get_copper_grids() - F.Cu and B.Cu as numpy arrays
    - get_pads() - pad info with ref, pin, net, position, shape, size, angle
    - get_tracks() - track segments
    - get_vias() - vias
    - get_nets() - net names
    - get_footprints() - component positions
    - get_board_edge() - real board outline from Edge.Cuts layer
    - capture_board() - all of the above in one call

Usage:
    from grab_layer import capture_board, find_socket

    socket = find_socket()
    data = capture_board(socket)
"""

import glob
import os

import numpy as np
from kipy import KiCad


# Layer constants (KiCad layer IDs)
BL_F_CU      = 3
BL_B_CU      = 34
BL_EDGE_CUTS = 44

# Known KiCad copper layer IDs in stack order
_KICAD_COPPER_IDS = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                     17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
                     30, 31, 32, 33, 34]

# PadStackShape enum values (kipy.board_types.PadStackShape)
PSS_UNKNOWN       = 0
PSS_CIRCLE        = 1
PSS_RECTANGLE     = 2
PSS_OVAL          = 3
PSS_TRAPEZOID     = 4
PSS_ROUNDRECT     = 5
PSS_CHAMFEREDRECT = 6
PSS_CUSTOM        = 7


def get_copper_layers(board):
    """Detect all copper layers from the board."""
    try:
        enabled = set(board.get_enabled_layers())
    except (AttributeError, Exception):
        try:
            n = board.get_copper_layer_count()
        except Exception:
            n = 2
        enabled = {BL_F_CU, BL_B_CU}
        for i in range(1, n - 1):
            enabled.add(BL_F_CU + i)

    copper = []
    for lid in _KICAD_COPPER_IDS:
        if lid in enabled:
            try:
                name = board.get_layer_name(lid)
            except Exception:
                name = f"In{lid - 3}.Cu" if lid not in (BL_F_CU, BL_B_CU) \
                    else ("F.Cu" if lid == BL_F_CU else "B.Cu")
            copper.append((lid, name))
    return copper


def find_socket():
    """Find the PCB editor socket in /tmp/kicad/."""
    sockets = glob.glob("/tmp/kicad/api-*.sock")
    if sockets:
        sockets.sort(key=lambda s: os.path.getmtime(s), reverse=True)
        for sock in sockets:
            try:
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


def get_board_edge(board):
    """
    Get board bounds from the Edge.Cuts layer (real board outline).
    Falls back to footprint bounds if no Edge.Cuts drawings found.
    """
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    found = False

    try:
        for drawing in board.get_drawings():
            if drawing.layer != BL_EDGE_CUTS:
                continue
            if hasattr(drawing, 'start') and hasattr(drawing, 'end'):
                for pt in (drawing.start, drawing.end):
                    x = pt.x / 1_000_000
                    y = pt.y / 1_000_000
                    min_x = min(min_x, x); max_x = max(max_x, x)
                    min_y = min(min_y, y); max_y = max(max_y, y)
                    found = True
            elif hasattr(drawing, 'center') and hasattr(drawing, 'radius'):
                cx = drawing.center.x / 1_000_000
                cy = drawing.center.y / 1_000_000
                r  = drawing.radius   / 1_000_000
                min_x = min(min_x, cx - r); max_x = max(max_x, cx + r)
                min_y = min(min_y, cy - r); max_y = max(max_y, cy + r)
                found = True
            elif hasattr(drawing, 'points'):
                for pt in drawing.points:
                    x = pt.x / 1_000_000
                    y = pt.y / 1_000_000
                    min_x = min(min_x, x); max_x = max(max_x, x)
                    min_y = min(min_y, y); max_y = max(max_y, y)
                    found = True
    except Exception as e:
        print(f"  get_board_edge: drawing scan failed ({e}), falling back to footprints")
        found = False

    if not found:
        print("  get_board_edge: no Edge.Cuts drawings found, falling back to footprint bounds")
        return get_board_bounds_from_footprints(board, margin=2.0)

    print(f"  board edge: ({min_x:.2f}, {min_y:.2f}) → ({max_x:.2f}, {max_y:.2f}) mm")
    return (min_x, min_y, max_x, max_y)


def get_board_bounds_from_footprints(board, margin=5.0):
    """Fallback bounds from footprint positions."""
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for fp in board.get_footprints():
        x = fp.position.x / 1_000_000
        y = fp.position.y / 1_000_000
        min_x = min(min_x, x); max_x = max(max_x, x)
        min_y = min(min_y, y); max_y = max(max_y, y)

    if min_x == float('inf'):
        return (0, 0, 100, 100)

    return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)


def get_board_bounds(board, margin=5.0):
    """Alias — prefers Edge.Cuts, falls back to footprint bounds."""
    return get_board_edge(board)


def _pad_shape_info(pad, fp_rotation=0.0):
    """
    Extract shape, width, height and angle from a pad's padstack.

    The total pad angle in screen coordinates is:
        pad.padstack.angle  (pad's own rotation within footprint)
        + fp_rotation       (footprint rotation on the board)

    Returns (shape_str, w_mm, h_mm, total_angle_deg) where shape_str is:
        'circle', 'rect', 'oval', 'roundrect', 'other'

    Each field is extracted independently so a failure in one does not
    affect the others.
    """
    shape = 'circle'
    w     = 0.5
    h     = 0.5
    angle = 0.0

    try:
        cls = pad.padstack.copper_layers
        if not cls:
            return shape, w, h, fp_rotation

        cl = cls[0]

        # ── Shape ────────────────────────────────────────────────────────
        try:
            s = int(cl.shape)
            if s == PSS_CIRCLE:
                shape = 'circle'
            elif s == PSS_RECTANGLE:
                shape = 'rect'
            elif s == PSS_OVAL:
                shape = 'oval'
            elif s in (PSS_ROUNDRECT, PSS_CHAMFEREDRECT):
                shape = 'roundrect'
            else:
                shape = 'other'
        except Exception:
            pass

        # ── Size ─────────────────────────────────────────────────────────
        try:
            sz = cl.size
            w  = round(sz.x / 1_000_000, 4)
            h  = round(sz.y / 1_000_000, 4)
        except Exception:
            pass

        # ── Angle: pad local rotation + footprint rotation ────────────────
        try:
            a = pad.padstack.angle
            pad_angle = round(float(a), 2)
        except Exception:
            pad_angle = 0.0

        angle = round((pad_angle + fp_rotation) % 360, 2)

    except Exception:
        pass

    return shape, w, h, angle


def get_pads(board, copper_layer_ids=None, raw_pads=None):
    """
    Get all pads from the board.

    Pad angle = pad's own padstack angle + footprint orientation.
    This gives the correct screen-space rotation for rendering.

    Returns:
        List of dicts: {ref, pin, net, x, y, smd, layers, shape, w, h, angle}

        shape: 'circle', 'rect', 'oval', 'roundrect', 'other'
        w, h:  pad width and height in mm (in footprint local coords)
        angle: total rotation in degrees (pad + footprint)
    """
    if copper_layer_ids is None:
        copper_layer_ids = {lid for lid, _ in get_copper_layers(board)}

    # Build pad-id → ref map AND footprint rotation map
    pad_ref_by_id = {}
    fp_rotation_by_ref = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if not ref:
            continue
        try:
            fp_rotation_by_ref[ref] = float(fp.orientation.degrees)
        except Exception:
            fp_rotation_by_ref[ref] = 0.0
        for fp_pad in fp.definition.pads:
            pad_ref_by_id[fp_pad.id.value] = ref

    pads = []
    pad_source = raw_pads if raw_pads is not None else board.get_pads()

    for pad in pad_source:
        x   = pad.position.x / 1_000_000
        y   = pad.position.y / 1_000_000
        net = pad.net.name if pad.net else ""
        pin = pad.number

        layers     = pad.padstack.layers
        pad_copper = [lid for lid in layers if lid in copper_layer_ids]
        smd        = (len(pad_copper) == 1)
        ref        = pad_ref_by_id.get(pad.id.value, "")

        fp_rot = fp_rotation_by_ref.get(ref, 0.0)
        shape, w, h, angle = _pad_shape_info(pad, fp_rotation=fp_rot)

        pads.append({
            "ref":    ref,
            "pin":    pin,
            "net":    net,
            "x":      round(x, 3),
            "y":      round(y, 3),
            "smd":    smd,
            "layers": list(layers),
            "shape":  shape,
            "w":      w,
            "h":      h,
            "angle":  angle,
        })

    return pads


def get_tracks(board, layer_name_map=None):
    """Get all tracks from the board."""
    if layer_name_map is None:
        layer_name_map = {lid: name for lid, name in get_copper_layers(board)}

    tracks = []
    for track in board.get_tracks():
        x1    = track.start.x / 1_000_000
        y1    = track.start.y / 1_000_000
        x2    = track.end.x   / 1_000_000
        y2    = track.end.y   / 1_000_000
        width = track.width   / 1_000_000
        layer = layer_name_map.get(track.layer, f"L{track.layer}")
        net   = track.net.name if track.net else ""

        tracks.append({
            "x1":    round(x1, 3),
            "y1":    round(y1, 3),
            "x2":    round(x2, 3),
            "y2":    round(y2, 3),
            "width": round(width, 3),
            "layer": layer,
            "net":   net,
        })
    return tracks


def get_vias(board):
    """Get all vias from the board."""
    vias = []
    for via in board.get_vias():
        x        = via.position.x / 1_000_000
        y        = via.position.y / 1_000_000
        od       = 0.6
        drill_id = 0.3
        try:
            if hasattr(via, 'diameter') and via.diameter:
                od = via.diameter / 1_000_000
            if hasattr(via, 'drill_diameter') and via.drill_diameter:
                drill_id = via.drill_diameter / 1_000_000
        except (AttributeError, TypeError):
            pass
        net = via.net.name if via.net else ""

        vias.append({
            "x":   round(x, 3),
            "y":   round(y, 3),
            "od":  round(od, 3),
            "id":  round(drill_id, 3),
            "net": net,
        })
    return vias


def get_nets(board):
    """Get all net names from the board."""
    return [net.name for net in board.get_nets() if net.name]


def get_design_rules(board):
    """Get design rules from KiCad board."""
    nets = list(board.get_nets())
    if not nets:
        return {"clearance": 0.2, "track_width": 0.2,
                "via_diameter": 0.6, "via_drill": 0.3}

    net_obj = None
    for n in nets:
        if n.name:
            net_obj = n
            break

    if not net_obj:
        return {"clearance": 0.2, "track_width": 0.2,
                "via_diameter": 0.6, "via_drill": 0.3}

    nc_map = board.get_netclass_for_nets([net_obj])
    nc     = nc_map.get(net_obj.name)

    if not nc:
        return {"clearance": 0.2, "track_width": 0.2,
                "via_diameter": 0.6, "via_drill": 0.3}

    return {
        "clearance":    nc.clearance    / 1_000_000 if nc.clearance    else 0.2,
        "track_width":  nc.track_width  / 1_000_000 if nc.track_width  else 0.2,
        "via_diameter": nc.via_diameter / 1_000_000 if nc.via_diameter else 0.6,
        "via_drill":    nc.via_drill    / 1_000_000 if nc.via_drill    else 0.3,
    }


def get_footprints(board):
    """Get all footprint positions."""
    footprints = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if not ref:
            continue
        x        = fp.position.x / 1_000_000
        y        = fp.position.y / 1_000_000
        rotation = fp.orientation.degrees if hasattr(fp.orientation, 'degrees') else 0
        footprints[ref] = {
            "x":        round(x, 3),
            "y":        round(y, 3),
            "rotation": rotation,
        }
    return footprints


def get_copper_grids(board, pitch_mm=0.1, bounds=None, copper_layers=None, raw_pads=None):
    """Get copper layer grids as numpy arrays with actual copper shapes."""
    if copper_layers is None:
        copper_layers = get_copper_layers(board)

    if bounds is None:
        bounds = get_board_edge(board)

    min_x, min_y, max_x, max_y = bounds
    width  = int((max_x - min_x) / pitch_mm) + 1
    height = int((max_y - min_y) / pitch_mm) + 1

    grids           = {}
    pad_owner_grids = {}
    lid_to_name     = {}
    for lid, name in copper_layers:
        grids[name]           = np.zeros((height, width), dtype=np.int8)
        pad_owner_grids[name] = np.zeros((height, width), dtype=np.int32)
        lid_to_name[lid]      = name

    def mm_to_grid(x_mm, y_mm):
        return int((x_mm - min_x) / pitch_mm), int((y_mm - min_y) / pitch_mm)

    def fill_cell(grid, gx, gy, value):
        if 0 <= gx < width and 0 <= gy < height:
            grid[gy, gx] = max(grid[gy, gx], value)

    def fill_polygon(grid, poly, value):
        points = []
        for node in poly.outline.nodes:
            gx, gy = mm_to_grid(node.point.x / 1e6, node.point.y / 1e6)
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
                    intersections.append(int(x1 + (gy - y1) * (x2 - x1) / (y2 - y1)))
            intersections.sort()
            for i in range(0, len(intersections) - 1, 2):
                for gx in range(max(0, intersections[i]),
                                min(width, intersections[i + 1] + 1)):
                    fill_cell(grid, gx, gy, value)

    def fill_polygon_owner(grid, poly, pad_index):
        points = []
        for node in poly.outline.nodes:
            gx, gy = mm_to_grid(node.point.x / 1e6, node.point.y / 1e6)
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
                    intersections.append(int(x1 + (gy - y1) * (x2 - x1) / (y2 - y1)))
            intersections.sort()
            for i in range(0, len(intersections) - 1, 2):
                for gx in range(max(0, intersections[i]),
                                min(width, intersections[i + 1] + 1)):
                    if 0 <= gx < width and 0 <= gy < height:
                        if grid[gy, gx] == 0:
                            grid[gy, gx] = pad_index

    def fill_circle(grid, cx, cy, radius_mm, value):
        gcx, gcy = mm_to_grid(cx, cy)
        gr = int(radius_mm / pitch_mm) + 1
        for dy in range(-gr, gr + 1):
            for dx in range(-gr, gr + 1):
                if dx * dx + dy * dy <= gr * gr:
                    fill_cell(grid, gcx + dx, gcy + dy, value)

    pads_iter = raw_pads if raw_pads is not None else list(board.get_pads())
    for pad_index, pad in enumerate(pads_iter, start=1):
        pad_layers = pad.padstack.layers
        pad_copper = [lid for lid in pad_layers if lid in lid_to_name]
        if not pad_copper:
            continue
        poly = board.get_pad_shapes_as_polygons(pad, layer=pad_copper[0])
        if not poly:
            continue
        for lid in pad_copper:
            layer_name = lid_to_name[lid]
            fill_polygon(grids[layer_name], poly, 1)
            fill_polygon_owner(pad_owner_grids[layer_name], poly, pad_index)

    for via in board.get_vias():
        x = via.position.x / 1e6
        y = via.position.y / 1e6
        od = 0.6
        try:
            if via.padstack.copper_layers:
                size = via.padstack.copper_layers[0].size
                od = size.x / 1e6 if hasattr(size, 'x') else size / 1e6
        except (AttributeError, TypeError):
            pass
        for name in grids:
            fill_circle(grids[name], x, y, od / 2, 3)

    bounds_dict = {
        'min_x': min_x, 'max_x': max_x,
        'min_y': min_y, 'max_y': max_y,
        'pitch_mm': pitch_mm,
        'width': width, 'height': height,
    }

    return grids, pad_owner_grids, bounds_dict


def capture_board(socket_path, pitch_mm=0.1):
    """Capture complete board state from KiCad."""
    import time as _time
    _t0 = _time.perf_counter()

    kicad, board = connect(socket_path)
    print(f"  connect: {_time.perf_counter()-_t0:.1f}s")

    copper_layers    = get_copper_layers(board)
    copper_layer_ids = {lid for lid, _ in copper_layers}
    layer_name_map   = {lid: name for lid, name in copper_layers}
    print(f"  get_copper_layers: {_time.perf_counter()-_t0:.1f}s")

    bounds = get_board_edge(board)
    print(f"  get_board_edge: {_time.perf_counter()-_t0:.1f}s")

    _raw_pads = list(board.get_pads())
    copper_grids, pad_owner_grids, bounds_dict = get_copper_grids(
        board, pitch_mm, bounds, copper_layers, raw_pads=_raw_pads)
    print(f"  copper_grids: {_time.perf_counter()-_t0:.1f}s")

    _pads       = get_pads(board, copper_layer_ids, raw_pads=_raw_pads)
    print(f"  get_pads: {_time.perf_counter()-_t0:.1f}s")
    _tracks     = get_tracks(board, layer_name_map)
    print(f"  get_tracks: {_time.perf_counter()-_t0:.1f}s")
    _vias       = get_vias(board)
    print(f"  get_vias: {_time.perf_counter()-_t0:.1f}s")
    _nets       = get_nets(board)
    _footprints = get_footprints(board)
    _rules      = get_design_rules(board)
    print(f"  nets/fps/rules: {_time.perf_counter()-_t0:.1f}s")

    result = {
        "pads":            _pads,
        "tracks":          _tracks,
        "vias":            _vias,
        "nets":            _nets,
        "footprints":      _footprints,
        "rules":           _rules,
        "copper_grids":    copper_grids,
        "pad_owner_grids": pad_owner_grids,
        "copper_layers":   [name for _, name in copper_layers],
        "bounds":          bounds_dict,
        "board_filename":  os.path.join(board.get_project().path, board.name),
    }
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
    """Convert copper grids to a PNG image as base64 data URL."""
    try:
        from PIL import Image
        import base64
        import io
    except ImportError:
        return None

    if isinstance(copper_grids_or_fcu, dict):
        copper_grids = copper_grids_or_fcu
        bounds_dict  = bcu_or_bounds
    else:
        copper_grids = {"F.Cu": copper_grids_or_fcu}
        if bcu_or_bounds is not None and not isinstance(bcu_or_bounds, dict):
            copper_grids["B.Cu"] = bcu_or_bounds

    if not copper_grids:
        return None

    first_grid    = next(iter(copper_grids.values()))
    height, width = first_grid.shape

    LAYER_COLORS = {
        "F.Cu": (255, 80, 80, 140),
        "B.Cu": (80, 80, 255, 140),
    }
    INNER_COLORS = [
        (220, 180,  40, 140),
        ( 40, 180,  80, 140),
        (180,  40, 180, 140),
        ( 40, 180, 180, 140),
    ]

    layer_names  = list(copper_grids.keys())
    layer_colors = {}
    inner_idx    = 0
    for name in layer_names:
        if name in LAYER_COLORS:
            layer_colors[name] = LAYER_COLORS[name]
        else:
            layer_colors[name] = INNER_COLORS[inner_idx % len(INNER_COLORS)]
            inner_idx += 1

    img    = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pixels = img.load()

    for y in range(height):
        for x in range(width):
            active = [n for n in layer_names if copper_grids[n][y, x] > 0]
            if not active:
                continue
            if len(active) > 1:
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
    print(f"  Pads:          {len(data['pads'])}")
    print(f"  Tracks:        {len(data['tracks'])}")
    print(f"  Vias:          {len(data['vias'])}")
    print(f"  Nets:          {len(data['nets'])}")
    print(f"  Footprints:    {len(data['footprints'])}")
    print(f"  Copper layers: {data['copper_layers']}")
    print(f"  Bounds:        ({data['bounds']['min_x']:.2f}, {data['bounds']['min_y']:.2f}) "
          f"→ ({data['bounds']['max_x']:.2f}, {data['bounds']['max_y']:.2f}) mm")

    print("\n=== Pad shapes ===")
    shape_counts = {}
    for p in data['pads']:
        shape_counts[p['shape']] = shape_counts.get(p['shape'], 0) + 1
    for s, c in sorted(shape_counts.items()):
        print(f"  {s}: {c} pads")

    print("\n=== XU1 pads ===")
    for p in data['pads']:
        if p['ref'] == 'XU1':
            print(f"  pin={p['pin']} shape={p['shape']} w={p['w']} h={p['h']} angle={p['angle']}")
            break