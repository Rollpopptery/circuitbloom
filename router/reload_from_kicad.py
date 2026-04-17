#!/usr/bin/env python3
"""
reload_from_kicad.py — Capture board state from KiCad via IPC API.

Standalone module with no dependency on route_state globals.
Returns a result dict that the caller applies to its own state.

Usage:
    from reload_from_kicad import capture_from_kicad

    result = capture_from_kicad(socket_path=None)
    if result["ok"]:
        grid        = result["grid"]
        state_patch = result["state"]
        kicad_socket = result["socket"]
        board_path   = result["board_path"]  # may be None
"""

import time

from dpcb_router_grid import GRID_PITCH

def _snap(v):
    return round(round(v / GRID_PITCH) * GRID_PITCH, 4)

def capture_from_kicad(socket_path=None, board_path_hint=None):
    """Capture board state from KiCad.

    Args:
        socket_path:      KiCad IPC socket path, or None to auto-detect
        board_path_hint:  current board_path so we only update if unset

    Returns:
        dict with keys:
            ok          — bool
            message     — str
            grid        — RouterGrid (or None on failure)
            socket      — socket path used
            board_path  — board filename if newly discovered, else None
            state       — dict of state fields to merge into route_state.state
    """
    # Reset cached component info
    try:
        from pad_info import reset as reset_pad_info
        reset_pad_info()
    except ImportError:
        pass

    try:
        from grab_layer import capture_board, find_socket, grid_to_png_base64
        from route_convert import net_color
        from route_state import build_router_grid_from_capture
        from dpcb_router_grid import GRID_PITCH
    except ImportError as e:
        return {"ok": False, "message": f"import error: {e}",
                "grid": None, "socket": None, "board_path": None, "state": None}

    def _snap(v):
        return round(round(v / GRID_PITCH) * GRID_PITCH, 4)

    # Auto-detect socket
    if socket_path is None:
        socket_path = find_socket()
    if not socket_path:
        return {"ok": False, "message": "no KiCad socket found in /tmp/kicad/",
                "grid": None, "socket": None, "board_path": None, "state": None}

    # Capture from KiCad
    try:
        data = capture_board(socket_path, pitch_mm=0.1)
    except Exception as e:
        return {"ok": False, "message": f"capture failed: {e}",
                "grid": None, "socket": socket_path, "board_path": None, "state": None}

    # Build net color map
    nets = {}
    for net_name in data["nets"]:
        nets[net_name] = net_color(net_name)

    # Board dimensions
    bounds = data["bounds"]
    origin_x = bounds["min_x"]
    origin_y = bounds["min_y"]
    board_w = bounds["max_x"] - origin_x
    board_h = bounds["max_y"] - origin_y

    # Offset and snap pads
    pads = []
    for p in data["pads"]:
        pads.append({
            "ref":  p["ref"],
            "pin":  p["pin"],
            "net":  p["net"],
            "x":    _snap(p["x"] - origin_x),
            "y":    _snap(p["y"] - origin_y),
            "smd":  p["smd"],
            "name": p.get("name", p["pin"]),
        })

    # Offset and snap tracks
    tracks = []
    for t in data["tracks"]:
        tracks.append({
            "x1":    _snap(t["x1"] - origin_x),
            "y1":    _snap(t["y1"] - origin_y),
            "x2":    _snap(t["x2"] - origin_x),
            "y2":    _snap(t["y2"] - origin_y),
            "width": t["width"],
            "layer": t["layer"],
            "net":   t["net"],
        })

    # Offset and snap vias
    vias = []
    for v in data["vias"]:
        vias.append({
            "x":   _snap(v["x"] - origin_x),
            "y":   _snap(v["y"] - origin_y),
            "od":  v["od"],
            "id":  v["id"],
            "net": v["net"],
        })

    # Offset and snap components
    components = {}
    for ref, pos in data["footprints"].items():
        components[ref] = {
            "x": _snap(pos["x"] - origin_x),
            "y": _snap(pos["y"] - origin_y),
        }

    # Heatmap PNG
    t0 = time.perf_counter()
    heatmap = None
    copper_grids = data.get("copper_grids")
    if copper_grids:
        heatmap = grid_to_png_base64(copper_grids, data["bounds"])
    elif "fcu" in data and "bcu" in data:
        heatmap = grid_to_png_base64(data["fcu"], data["bcu"], data["bounds"])
    print(f"  heatmap PNG: {time.perf_counter()-t0:.1f}s")

    # Router grid
    t0 = time.perf_counter()
    new_grid = build_router_grid_from_capture(data, origin_x, origin_y, board_w, board_h)
    print(f"  router grid: {time.perf_counter()-t0:.1f}s")

    # Board filename — only update if not already set
    board_file = data.get("board_filename")
    new_board_path = None
    if board_file and not board_path_hint:
        new_board_path = board_file
        print(f"  Board file: {new_board_path}")

    n_pads   = len(data["pads"])
    n_tracks = len(data["tracks"])
    n_vias   = len(data["vias"])
    n_nets   = len(data["nets"])
    print(f"  KiCad capture: {n_pads} pads, {n_tracks} tracks, {n_vias} vias, {n_nets} nets")

    return {
        "ok":      True,
        "message": f"captured {n_pads} pads, {n_tracks} tracks, {n_vias} vias, {n_nets} nets",
        "grid":    new_grid,
        "socket":  socket_path,
        "board_path": new_board_path,
        "state": {
            "pads":   pads,
            "tracks": tracks,
            "vias":   vias,
            "board": {
                "width":          board_w,
                "height":         board_h,
                "rules":          data.get("rules", {}),
                "origin_x":       origin_x,
                "origin_y":       origin_y,
                "heatmap_bounds": bounds,
                "copper_layers":  data.get("copper_layers", ["F.Cu", "B.Cu"]),
            },
            "nets":       nets,
            "components": components,
            "rects":      [],
            "heatmap":    heatmap,
        },
    }