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


# Layer constants
BL_F_CU = 3
BL_B_CU = 34


def find_socket():
    """Find the PCB editor socket in /tmp/kicad/.

    Returns the numbered socket (api-XXXXX.sock) which is the PCB editor.
    The plain api.sock is the KiCad launcher and doesn't have board handlers.
    """
    sockets = glob.glob("/tmp/kicad/api-*.sock")
    if sockets:
        return f"ipc://{sockets[0]}"
    return None


def connect(socket_path):
    """Connect to KiCad and return (kicad, board) tuple."""
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()
    return kicad, board


def get_pads(board):
    """
    Get all pads from the board.

    Returns:
        List of dicts: {ref, pin, net, x, y, smd, layers}
    """
    pads = []

    # Build footprint position map for matching pads to footprints
    fp_positions = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if ref:
            x = fp.position.x / 1000000
            y = fp.position.y / 1000000
            fp_positions[ref] = (x, y)

    for pad in board.get_pads():
        x = pad.position.x / 1000000
        y = pad.position.y / 1000000
        net = pad.net.name if pad.net else ""
        pin = pad.number

        # Get layers
        layers = pad.padstack.layers
        on_fcu = BL_F_CU in layers
        on_bcu = BL_B_CU in layers

        # SMD if only on one copper layer
        smd = (on_fcu != on_bcu)

        # Match pad to nearest footprint
        ref = ""
        best_dist = float('inf')
        for fp_ref, (fx, fy) in fp_positions.items():
            d = ((x - fx)**2 + (y - fy)**2)**0.5
            if d < best_dist:
                best_dist = d
                ref = fp_ref

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


def get_tracks(board):
    """
    Get all tracks from the board.

    Returns:
        List of dicts: {x1, y1, x2, y2, width, layer, net}
    """
    tracks = []

    for track in board.get_tracks():
        x1 = track.start.x / 1000000
        y1 = track.start.y / 1000000
        x2 = track.end.x / 1000000
        y2 = track.end.y / 1000000
        width = track.width / 1000000
        layer = "F.Cu" if track.layer == BL_F_CU else "B.Cu" if track.layer == BL_B_CU else f"L{track.layer}"
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


def get_copper_grids(board, pitch_mm=0.1, bounds=None):
    """
    Get F.Cu and B.Cu as numpy grids with actual copper shapes.

    Args:
        board: KiCad board object
        pitch_mm: Grid resolution in mm (default 0.1mm)
        bounds: Optional (min_x, min_y, max_x, max_y) in mm

    Returns:
        fcu_grid: numpy array for front copper
        bcu_grid: numpy array for back copper
        bounds_dict: dict with min_x, max_x, min_y, max_y, pitch_mm, width, height

    Grid values:
        0 = empty
        1 = pad copper
        2 = track copper
        3 = via copper
    """
    if bounds is None:
        bounds = get_board_bounds(board)

    min_x, min_y, max_x, max_y = bounds
    width = int((max_x - min_x) / pitch_mm) + 1
    height = int((max_y - min_y) / pitch_mm) + 1

    fcu = np.zeros((height, width), dtype=np.int8)
    bcu = np.zeros((height, width), dtype=np.int8)

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

    def fill_thick_line(grid, x1, y1, x2, y2, width_mm, value):
        """Rasterize a track with its actual width."""
        gx1, gy1 = mm_to_grid(x1, y1)
        gx2, gy2 = mm_to_grid(x2, y2)
        hw = int((width_mm / 2) / pitch_mm) + 1

        steps = max(abs(gx2 - gx1), abs(gy2 - gy1), 1)
        for i in range(steps + 1):
            t = i / steps
            gx = int(gx1 + t * (gx2 - gx1))
            gy = int(gy1 + t * (gy2 - gy1))
            for dy in range(-hw, hw + 1):
                for dx in range(-hw, hw + 1):
                    fill_cell(grid, gx + dx, gy + dy, value)

    def fill_circle(grid, cx, cy, radius_mm, value):
        """Rasterize a circular via/pad."""
        gcx, gcy = mm_to_grid(cx, cy)
        gr = int(radius_mm / pitch_mm) + 1

        for dy in range(-gr, gr + 1):
            for dx in range(-gr, gr + 1):
                if dx * dx + dy * dy <= gr * gr:
                    fill_cell(grid, gcx + dx, gcy + dy, value)

    # Rasterize pads with actual shapes
    for pad in board.get_pads():
        layers = pad.padstack.layers
        if BL_F_CU in layers:
            poly = board.get_pad_shapes_as_polygons(pad, layer=BL_F_CU)
            if poly:
                fill_polygon(fcu, poly, 1)
        if BL_B_CU in layers:
            poly = board.get_pad_shapes_as_polygons(pad, layer=BL_B_CU)
            if poly:
                fill_polygon(bcu, poly, 1)

    # Rasterize tracks with actual width
    for track in board.get_tracks():
        x1, y1 = track.start.x / 1e6, track.start.y / 1e6
        x2, y2 = track.end.x / 1e6, track.end.y / 1e6
        w = track.width / 1e6

        if track.layer == BL_F_CU:
            fill_thick_line(fcu, x1, y1, x2, y2, w, 2)
        elif track.layer == BL_B_CU:
            fill_thick_line(bcu, x1, y1, x2, y2, w, 2)

    # Rasterize vias with actual size
    for via in board.get_vias():
        x = via.position.x / 1e6
        y = via.position.y / 1e6
        # Get via outer diameter from padstack
        od = 0.6  # default
        try:
            if via.padstack.copper_layers:
                size = via.padstack.copper_layers[0].size
                # size might be Vector2 or scalar
                if hasattr(size, 'x'):
                    od = size.x / 1e6
                else:
                    od = size / 1e6
        except (AttributeError, TypeError):
            pass  # use default
        radius = od / 2

        fill_circle(fcu, x, y, radius, 3)
        fill_circle(bcu, x, y, radius, 3)

    bounds_dict = {
        'min_x': min_x, 'max_x': max_x,
        'min_y': min_y, 'max_y': max_y,
        'pitch_mm': pitch_mm,
        'width': width, 'height': height
    }

    return fcu, bcu, bounds_dict


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
    kicad, board = connect(socket_path)

    bounds = get_board_bounds(board)
    fcu, bcu, bounds_dict = get_copper_grids(board, pitch_mm, bounds)

    return {
        "pads": get_pads(board),
        "tracks": get_tracks(board),
        "vias": get_vias(board),
        "nets": get_nets(board),
        "footprints": get_footprints(board),
        "rules": get_design_rules(board),
        "fcu": fcu,
        "bcu": bcu,
        "bounds": bounds_dict,
        "board_filename": os.path.join(board.get_project().path, board.name),
    }


def print_grid(grid, legend=None):
    """Print grid as ASCII art."""
    if legend is None:
        legend = {0: '.', 1: '#', 2: '=', 3: 'O'}

    for y in range(grid.shape[0]):
        row = ""
        for x in range(grid.shape[1]):
            row += legend.get(grid[y, x], '?')
        print(row)


def grid_to_png_base64(fcu, bcu, bounds_dict):
    """Convert copper grids to a PNG image as base64 data URL.

    Args:
        fcu: numpy array for F.Cu (0=empty, 1=pad, 2=track, 3=via)
        bcu: numpy array for B.Cu
        bounds_dict: dict with width, height, pitch_mm, etc.

    Returns:
        Base64 data URL string for the PNG image
    """
    try:
        from PIL import Image
        import base64
        import io
    except ImportError:
        return None

    height, width = fcu.shape

    # Create RGBA image
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    pixels = img.load()

    # Color scheme:
    # F.Cu only: red (255, 80, 80)
    # B.Cu only: blue (80, 80, 255)
    # Both layers: purple (200, 80, 200)
    # Transparency: 160 for copper, 0 for empty

    for y in range(height):
        for x in range(width):
            f = fcu[y, x]
            b = bcu[y, x]
            if f > 0 and b > 0:
                # Both layers - purple
                pixels[x, y] = (200, 80, 200, 160)
            elif f > 0:
                # F.Cu only - red
                pixels[x, y] = (255, 80, 80, 140)
            elif b > 0:
                # B.Cu only - blue
                pixels[x, y] = (80, 80, 255, 140)
            # else: transparent (default)

    # Convert to base64 data URL
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
    print(f"  Grid: {data['bounds']['width']}x{data['bounds']['height']} at {data['bounds']['pitch_mm']}mm")
    print(f"  Bounds: ({data['bounds']['min_x']:.1f}, {data['bounds']['min_y']:.1f}) to ({data['bounds']['max_x']:.1f}, {data['bounds']['max_y']:.1f}) mm")
    print(f"  F.Cu cells: {np.count_nonzero(data['fcu'])}")
    print(f"  B.Cu cells: {np.count_nonzero(data['bcu'])}")

    print("\n=== Sample pads ===")
    for p in data['pads'][:5]:
        print(f"  {p['ref']}.{p['pin']}: ({p['x']}, {p['y']}) net={p['net']}")

    print("\n=== F.Cu (0.5mm pitch) ===")
    print("Legend: . = empty, # = pad, = = track, O = via")
    print_grid(data['fcu'])
