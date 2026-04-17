#!/usr/bin/env python3
"""
freespace_endpoints.py — HTTP handlers for /freespace/* and /keepouts/* endpoints.

Each handler receives a BoardSVG instance and returns (status, content_type, body).
All Shapely geometry is delegated to freespace.py.
No HTTP server knowledge.

Keepout strategy:
    For any two pads of the same component whose centre-to-centre distance
    is less than max_dist, place a rectangle PERPENDICULAR to the pad-to-pad
    axis at the midpoint between them — BUT ONLY if no other pad of the same
    component lies between them (true adjacency check).

    Pad elements may be <circle>, <rect>, or <ellipse> depending on pad shape.
    Centre position is extracted correctly for each tag type.
"""

import json
import math
from board_svg import BoardSVG, SVGElement
from freespace import (
    compute_freespace, polygon_to_path,
    all_corners, sweep_lines, find_crossings,
)


# ── Pad helpers ───────────────────────────────────────────────────────────────

def _is_pad(el: SVGElement) -> bool:
    """True if this element is a pad (has data-ref attribute)."""
    return (
        el.tag in ('circle', 'rect', 'ellipse')
        and el.attrs.get('data-ref') is not None
        and el.attrs.get('data-pin') is not None
    )


def _pad_centre(el: SVGElement) -> tuple[float, float]:
    """Extract (cx, cy) centre from any pad element shape."""
    if el.tag in ('circle', 'ellipse'):
        return float(el.attrs.get('cx', 0)), float(el.attrs.get('cy', 0))
    elif el.tag == 'rect':
        x = float(el.attrs.get('x', 0))
        y = float(el.attrs.get('y', 0))
        w = float(el.attrs.get('width',  0))
        h = float(el.attrs.get('height', 0))
        return round(x + w / 2, 4), round(y + h / 2, 4)
    return 0.0, 0.0


def _pads_from_board(board: BoardSVG) -> list[dict]:
    """Extract pad centre points from the board's pads group."""
    result = []
    for el in board.pads_g.children:
        if not _is_pad(el):
            continue
        cx, cy = _pad_centre(el)
        result.append({"x": cx, "y": cy})
    return result


def _traces_from_board(board: BoardSVG) -> list[dict]:
    """Extract line and polyline segments from the overlay group."""
    traces = []
    for el in board.overlay_g.children:
        if el.tag == 'line':
            try:
                traces.append({
                    "x1": float(el.attrs['x1']), "y1": float(el.attrs['y1']),
                    "x2": float(el.attrs['x2']), "y2": float(el.attrs['y2']),
                })
            except (KeyError, ValueError):
                pass
        elif el.tag == 'polyline':
            points_str = el.attrs.get('points', '')
            pts = []
            for pair in points_str.strip().split():
                try:
                    x, y = pair.split(',')
                    pts.append((float(x), float(y)))
                except ValueError:
                    pass
            for i in range(len(pts) - 1):
                traces.append({
                    "x1": pts[i][0],   "y1": pts[i][1],
                    "x2": pts[i+1][0], "y2": pts[i+1][1],
                })
    return traces


# ── Endpoint handlers ─────────────────────────────────────────────────────────

def handle_freespace_draw(board: BoardSVG,
                          clearance: float = 1.27) -> tuple[int, str, bytes]:
    """GET /freespace/draw"""
    if board is None:
        return 404, "application/json", _err("no board loaded")

    pads       = _pads_from_board(board)
    traces     = _traces_from_board(board)
    free_space = compute_freespace(pads, board.board_w, board.board_h,
                                   clearance, traces=traces)
    d          = polygon_to_path(free_space)

    board.add_element("path", {
        "id":           "freespace",
        "d":            d,
        "fill":         "#00ff0022",
        "stroke":       "#00ff00",
        "stroke-width": "0.05",
        "fill-rule":    "evenodd",
    })

    return 200, "application/json", _json({"ok": True, "clearance": clearance})


def handle_freespace_lines(board: BoardSVG,
                           clearance: float = 1.27) -> tuple[int, str, bytes]:
    """GET /freespace/lines"""
    if board is None:
        return 404, "application/json", _err("no board loaded")

    pads       = _pads_from_board(board)
    traces     = _traces_from_board(board)
    free_space = compute_freespace(pads, board.board_w, board.board_h,
                                   clearance, traces=traces)
    free_space = free_space.simplify(0.1)

    d = polygon_to_path(free_space if hasattr(free_space, 'exterior')
                        else list(free_space.geoms)[0])
    board.add_element("path", {
        "d": d, "fill": "#00ff0022", "stroke": "#00ff00",
        "stroke-width": "0.05", "fill-rule": "evenodd",
    })

    corners  = all_corners(free_space)
    segments, h_lines, v_lines = sweep_lines(
        corners, free_space, board.board_w, board.board_h)
    crossings = find_crossings(h_lines, v_lines)

    for x, y, _ext in corners:
        board.add_element("circle", {
            "cx": str(x), "cy": str(y), "r": "0.3",
            "fill": "#0088ff", "stroke": "none",
        })
    for s in segments:
        board.add_element("line", {
            "x1": str(s["x1"]), "y1": str(s["y1"]),
            "x2": str(s["x2"]), "y2": str(s["y2"]),
            "stroke": "#ff880088", "stroke-width": "0.08",
        })
    for x, y in crossings:
        board.add_element("circle", {
            "cx": str(x), "cy": str(y), "r": "0.25",
            "fill": "#ff0000", "stroke": "none",
        })

    return 200, "application/json", _json({
        "ok":        True,
        "corners":   len(corners),
        "segments":  len(segments),
        "crossings": len(crossings),
    })


def handle_keepouts_draw(board: BoardSVG,
                         max_dist: float = 2.0,
                         width: float = 0.2,
                         height: float = 0.6,
                         length: float = 0.0,
                         ref: str = None) -> tuple[int, str, bytes]:
    """
    GET /keepouts/draw?max_dist=2.0&width=0.2&height=0.6&length=1.0&ref=XU1

    For any two pads of the same component whose centre-to-centre distance
    is less than max_dist, place a rectangle perpendicular to the pad-to-pad
    axis at the midpoint — only if no other pad lies between them.

    Supports pad elements of any shape: <circle>, <rect>, <ellipse>.
    Centre position is extracted correctly for each tag type.
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")

    # Collect pad elements filtered by ref
    pad_els = [
        el for el in board.pads_g.children
        if _is_pad(el)
        and (ref is None or el.attrs.get('data-ref', '') == ref)
    ]

    if not pad_els:
        return 404, "application/json", _err(
            f"no pads found{' for ref=' + ref if ref else ''}")

    from collections import defaultdict
    by_ref: dict[str, list] = defaultdict(list)
    for el in pad_els:
        by_ref[el.attrs.get('data-ref', '')].append(el)

    count   = 0
    skipped = 0

    for comp_ref, comp_pads in by_ref.items():
        n = len(comp_pads)
        if n < 2:
            continue

        # Pre-extract centres for fast adjacency check
        positions = [_pad_centre(el) for el in comp_pads]

        for i in range(n):
            for j in range(i + 1, n):
                ax, ay = positions[i]
                bx, by_ = positions[j]

                dist = math.sqrt((bx - ax) ** 2 + (by_ - ay) ** 2)
                if dist >= max_dist:
                    continue

                mx = (ax + bx) / 2
                my = (ay + by_) / 2

                # Adjacency check — skip if any other pad lies between A and B
                half_dist = dist / 2
                is_adjacent = True
                for k in range(n):
                    if k == i or k == j:
                        continue
                    cx, cy = positions[k]
                    d_to_mid = math.sqrt((cx - mx) ** 2 + (cy - my) ** 2)
                    if d_to_mid < half_dist - 0.1:
                        is_adjacent = False
                        break

                if not is_adjacent:
                    skipped += 1
                    continue

                # Build rotated keepout rectangle
                angle = math.atan2(by_ - ay, bx - ax)
                cos_a = math.cos(angle)
                sin_a = math.sin(angle)

                hw = width / 2              # thin along pad-to-pad axis
                hh = (height / 2) + length  # grows perpendicular, away from body

                corners = [
                    (mx + hw * cos_a - hh * sin_a,
                     my + hw * sin_a + hh * cos_a),
                    (mx + hw * cos_a + hh * sin_a,
                     my + hw * sin_a - hh * cos_a),
                    (mx - hw * cos_a + hh * sin_a,
                     my - hw * sin_a - hh * cos_a),
                    (mx - hw * cos_a - hh * sin_a,
                     my - hw * sin_a + hh * cos_a),
                ]

                points_str = ' '.join(
                    f"{round(x, 4)},{round(y, 4)}" for x, y in corners
                )

                layer = comp_pads[i].attrs.get('data-layer', 'F.Cu')
                pin_a = comp_pads[i].attrs.get('data-pin', '')
                pin_b = comp_pads[j].attrs.get('data-pin', '')

                board.add_element("polygon", {
                    "id":           f"keepout-{comp_ref}-{pin_a}-{pin_b}",
                    "points":       points_str,
                    "fill":         "#ff440033",
                    "stroke":       "#ff6600",
                    "stroke-width": "0.03",
                    "data-ref":     comp_ref,
                    "data-pin":     pin_a,
                    "data-keepout": "1",
                    "data-layer":   layer,
                    "data-dist":    str(round(dist, 3)),
                })
                count += 1

    return 200, "application/json", _json({
        "ok":       True,
        "count":    count,
        "skipped":  skipped,
        "max_dist": max_dist,
        "width":    width,
        "height":   height,
        "length":   length,
        "ref":      ref or "all",
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(data: dict) -> bytes:
    return json.dumps(data).encode()

def _err(msg: str) -> bytes:
    return json.dumps({"ok": False, "error": msg}).encode()