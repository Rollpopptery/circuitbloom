#!/usr/bin/env python3
"""
kicad_push.py — Push routed tracks and vias back to KiCad handler.

Extracts routed tracks and vias from the current board overlays and
pushes them to KiCad via IPC using kicad_route.push_routes().

Usage:
    from kicad_push import push_to_kicad_handler
    code, ct, body = push_to_kicad_handler(socket_path)
"""

import json
import server_state as ss
from grab_layer import find_socket


def push_to_kicad_handler(socket_path: str = None) -> tuple[int, str, bytes]:
    """
    Push routed tracks and vias from current board overlays to KiCad.

    Args:
        socket_path: KiCad IPC socket path, or None to auto-detect.

    Returns:
        (status_code, content_type, body)
    """
    with ss.board_lock:
        if ss.current_board is None:
            return 404, 'application/json', \
                b'{"ok":false,"error":"no board loaded"}'

        tracks   = ss.current_board.extract_routed_tracks()
        vias     = ss.current_board.extract_routed_vias()
        origin_x = ss.current_board.origin_x
        origin_y = ss.current_board.origin_y

    if not tracks and not vias:
        return 400, 'application/json', \
            b'{"ok":false,"error":"no routed tracks or vias to push"}'

    sp = socket_path or find_socket()
    if not sp:
        return 500, 'application/json', \
            b'{"ok":false,"error":"no KiCad socket found"}'

    try:
        from kicad_route import push_routes
        ok, msg = push_routes(sp, tracks, vias, origin_x, origin_y)
    except Exception as e:
        return 500, 'application/json', \
            json.dumps({"ok": False, "error": str(e)}).encode()

    print(f"  push: {msg}")
    code = 200 if ok else 500
    return code, 'application/json', json.dumps({
        "ok":     ok,
        "message": msg,
        "tracks": len(tracks),
        "vias":   len(vias),
    }).encode()