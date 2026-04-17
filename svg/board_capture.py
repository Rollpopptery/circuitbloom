#!/usr/bin/env python3
"""
board_capture.py — KiCad board capture handler for the SVG server.

Connects to KiCad via IPC, captures the board state, builds BoardSVG
and CorridorMap, and updates server_state.

Usage:
    from board_capture import capture_board_handler
    code, ct, body = capture_board_handler(socket_path)
"""

import json
import server_state as ss
from grab_layer import capture_board, find_socket
from board_svg import BoardSVG
from corridor_map import CorridorMap


def capture_board_handler(socket_path: str = None) -> tuple[int, str, bytes]:
    """
    Capture board state from KiCad and update server_state.

    Args:
        socket_path: KiCad IPC socket path, or None to auto-detect.

    Returns:
        (status_code, content_type, body) — body is the SVG on success
        or a JSON error on failure.
    """
    sp = socket_path or find_socket()
    if not sp:
        return 500, 'application/json', \
            b'{"ok":false,"error":"no KiCad socket found"}'

    try:
        data = capture_board(sp, pitch_mm=0.5)
    except Exception as e:
        return 500, 'application/json', \
            f'{{"ok":false,"error":{str(e)!r}}}'.encode()

    origin_x = data["bounds"]["min_x"]
    origin_y = data["bounds"]["min_y"]
    board_w  = data["bounds"]["max_x"] - origin_x
    board_h  = data["bounds"]["max_y"] - origin_y

    offset_pads = [
        {"ref": p["ref"], "pin": p["pin"], "net": p.get("net", ""),
         "x": round(p["x"] - origin_x, 4), "y": round(p["y"] - origin_y, 4)}
        for p in data["pads"]
    ]

    board     = BoardSVG.from_capture(data)
    corridors = CorridorMap.from_state(
        {"pads": offset_pads}, {"width": board_w, "height": board_h})

    with ss.board_lock:
        ss.current_board     = board
        ss.current_corridors = corridors

    print(f"  captured: {len(data['pads'])} pads, "
          f"{len(data['tracks'])} tracks, {len(data['vias'])} vias, "
          f"{len(corridors.corridors)} corridors")

    return 200, 'image/svg+xml', board.to_svg().encode()