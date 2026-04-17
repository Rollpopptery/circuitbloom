#!/usr/bin/env python3
"""
corridor_endpoints.py — HTTP handlers for /corridors/* endpoints.

Each handler receives BoardSVG and CorridorMap instances and returns
(status, content_type, body).
No HTTP server knowledge, no Shapely dependency.
"""

import json
from board_svg import BoardSVG
from corridor_map import CorridorMap


CORRIDOR_COLOURS = [
    "#ff000033", "#00ff0033", "#0000ff33", "#ffff0033",
    "#ff00ff33", "#00ffff33", "#ff880033", "#8800ff33",
    "#00ff8833", "#ff008833", "#88ff0033", "#0088ff33",
]


def _pads_from_board(board: BoardSVG) -> list[dict]:
    return [
        {
            "ref": el.attrs.get('data-ref', ''),
            "pin": el.attrs.get('data-pin', ''),
            "net": el.attrs.get('data-net', ''),
            "x":   float(el.attrs.get('cx', 0)),
            "y":   float(el.attrs.get('cy', 0)),
        }
        for el in board.pads_g.children
        if el.tag == 'circle'
    ]


def handle_corridors_draw(board: BoardSVG, corridors: CorridorMap,
                          clearance: float = 1.27) -> tuple[int, str, bytes]:
    """
    GET /corridors/draw — draw corridor rectangles and labels into overlays.
    Rebuilds CorridorMap at the requested clearance.
    """
    if board is None or corridors is None:
        return 404, "application/json", _err("no board loaded")

    import corridor_map as _cm
    orig = _cm.CLEARANCE
    _cm.CLEARANCE = clearance
    cmap = _cm.CorridorMap.from_state(
        {"pads": _pads_from_board(board)},
        {"width": board.board_w, "height": board.board_h},
    )
    _cm.CLEARANCE = orig

    for i, c in enumerate(cmap.corridors):
        colour = CORRIDOR_COLOURS[i % len(CORRIDOR_COLOURS)]
        board.add_element("rect", {
            "id":           f"corridor-{c.name}",
            "x":            str(c.x1), "y": str(c.y1),
            "width":        str(c.w),  "height": str(c.h),
            "fill":         colour,
            "stroke":       "#ffffff",
            "stroke-width": "0.1",
            "data-corridor": c.name,
        })
        board.add_element("text", {
            "x":                 str(c.cx),
            "y":                 str(c.cy),
            "font-size":         "0.8",
            "fill":              "#ffffff",
            "text-anchor":       "middle",
            "dominant-baseline": "central",
            "_text":             c.name,
        })

    return 200, "application/json", _json({
        "ok":       True,
        "count":    len(cmap.corridors),
        "clearance": clearance,
        "corridors": [c.name for c in cmap.corridors],
    })


def handle_corridors_list(corridors: CorridorMap) -> tuple[int, str, bytes]:
    """GET /corridors — all corridors as JSON."""
    if corridors is None:
        return 404, "application/json", _err("no board loaded")
    return 200, "application/json", _json({
        "ok":       True,
        "count":    len(corridors.corridors),
        "corridors": corridors.to_json(),
    })


def handle_corridors_describe(corridors: CorridorMap) -> tuple[int, str, bytes]:
    """GET /corridors/describe — human/LLM readable description."""
    if corridors is None:
        return 404, "application/json", _err("no board loaded")
    return 200, "text/plain", corridors.describe().encode()


def handle_corridors_path(board: BoardSVG, corridors: CorridorMap,
                          from_pad: str, to_pad: str) -> tuple[int, str, bytes]:
    """GET /corridors/path?from=ref:pin&to=ref:pin — corridor path between two pads."""
    if board is None or corridors is None:
        return 404, "application/json", _err("no board loaded")

    def find_pad(ref_pin: str) -> dict | None:
        ref, pin = ref_pin.split(':') if ':' in ref_pin else (ref_pin, '')
        for el in board.pads_g.children:
            if el.attrs.get('data-ref') == ref and el.attrs.get('data-pin') == pin:
                return {"ref": ref, "pin": pin,
                        "x": float(el.attrs['cx']),
                        "y": float(el.attrs['cy'])}
        return None

    pa = find_pad(from_pad)
    pb = find_pad(to_pad)
    if not pa or not pb:
        return 404, "application/json", _err(
            f"pad not found: {from_pad} or {to_pad}")

    return 200, "application/json", _json({
        "ok":          True,
        "path":        corridors.path_for_pads(pa, pb),
        "description": corridors.describe_path(pa, pb),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(data: dict) -> bytes:
    return json.dumps(data).encode()

def _err(msg: str) -> bytes:
    return json.dumps({"ok": False, "error": msg}).encode()