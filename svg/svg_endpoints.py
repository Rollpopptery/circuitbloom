#!/usr/bin/env python3
"""
svg_endpoints.py — HTTP endpoint handlers for BoardSVG manipulation.

All handlers operate on a shared BoardSVG instance provided by the server.
No KiCad dependency, no HTTP server knowledge — just request parsing
and BoardSVG operations.

Each handler receives (path, params, body) and returns (status, content_type, body).
"""

import json
from board_svg import BoardSVG, SVGElement


def handle_get_svg(board: BoardSVG, compact: bool = False) -> tuple[int, str, bytes]:
    """GET /svg — return current board as SVG string.

    Args:
        compact: if True, strip pad labels and whitespace for LLM consumption
    """
    if board is None:
        return 404, "application/json", _err("no board loaded — call /board.svg first")
    svg = board.to_svg_compact() if compact else board.to_svg()
    return 200, "image/svg+xml", svg.encode()


def handle_get_element(board: BoardSVG, element_id: str) -> tuple[int, str, bytes]:
    """GET /svg/element/{id} — return a single element serialised as SVG."""
    if board is None:
        return 404, "application/json", _err("no board loaded")
    from board_svg import _element_to_svg
    el = board.get_element(element_id)
    if el is None:
        return 404, "application/json", _err(f"element not found: {element_id}")
    return 200, "image/svg+xml", _element_to_svg(el).encode()


def handle_query_net(board: BoardSVG, net: str) -> tuple[int, str, bytes]:
    """GET /svg/net/{net} — return all elements belonging to a net as JSON."""
    if board is None:
        return 404, "application/json", _err("no board loaded")
    elements = board.get_by_net(net)
    result = [_element_summary(e) for e in elements]
    return 200, "application/json", _json({"net": net, "count": len(result), "elements": result})


def handle_query_ref(board: BoardSVG, ref: str) -> tuple[int, str, bytes]:
    """GET /svg/ref/{ref} — return all pad elements for a component ref."""
    if board is None:
        return 404, "application/json", _err("no board loaded")
    elements = board.get_by_ref(ref)
    result = [_element_summary(e) for e in elements]
    return 200, "application/json", _json({"ref": ref, "count": len(result), "elements": result})


def handle_add(board: BoardSVG, body: bytes) -> tuple[int, str, bytes]:
    """
    POST /svg/add — add a new SVG element.

    Body JSON:
        {
            "tag":       "circle",
            "attrs":     {"cx": 10, "cy": 20, "r": 0.5, "fill": "#ff0000", "id": "marker-1"},
            "parent_id": "overlays"   // optional, defaults to overlays
        }
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")
    try:
        cmd = json.loads(body)
    except Exception as e:
        return 400, "application/json", _err(f"invalid JSON: {e}")

    tag       = cmd.get("tag", "")
    attrs     = cmd.get("attrs", {})
    parent_id = cmd.get("parent_id", "overlays")

    if not tag:
        return 400, "application/json", _err("tag is required")

    el = board.add_element(tag, attrs, parent_id)
    return 200, "application/json", _json({
        "ok":  True,
        "id":  el.attrs.get("id", ""),
        "tag": el.tag,
    })


def handle_update(board: BoardSVG, body: bytes) -> tuple[int, str, bytes]:
    """
    POST /svg/update — update attrs on an existing element.

    Body JSON:
        {
            "id":    "pad-U1-3",
            "attrs": {"fill": "#ff0000", "r": "0.4"}
        }
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")
    try:
        cmd = json.loads(body)
    except Exception as e:
        return 400, "application/json", _err(f"invalid JSON: {e}")

    element_id = cmd.get("id", "")
    attrs      = cmd.get("attrs", {})

    if not element_id:
        return 400, "application/json", _err("id is required")

    ok = board.update_element(element_id, attrs)
    if not ok:
        return 404, "application/json", _err(f"element not found: {element_id}")
    return 200, "application/json", _json({"ok": True, "id": element_id})


def handle_remove(board: BoardSVG, body: bytes) -> tuple[int, str, bytes]:
    """
    POST /svg/remove — remove an element by id.

    Body JSON:
        {"id": "marker-1"}
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")
    try:
        cmd = json.loads(body)
    except Exception as e:
        return 400, "application/json", _err(f"invalid JSON: {e}")

    element_id = cmd.get("id", "")
    if not element_id:
        return 400, "application/json", _err("id is required")

    ok = board.remove_element(element_id)
    if not ok:
        return 404, "application/json", _err(f"element not found: {element_id}")
    return 200, "application/json", _json({"ok": True, "id": element_id})


def handle_clear_overlays(board: BoardSVG) -> tuple[int, str, bytes]:
    """POST /svg/clear_overlays — remove all overlay elements."""
    if board is None:
        return 404, "application/json", _err("no board loaded")
    board.clear_overlays()
    return 200, "application/json", _json({"ok": True})


def handle_info(board: BoardSVG) -> tuple[int, str, bytes]:
    """GET /svg/info — summary of current board object."""
    if board is None:
        return 404, "application/json", _err("no board loaded")
    n_tracks = sum(len(g.children) for g in board.layers.values())
    n_pads   = sum(1 for c in board.pads_g.children if c.tag == "circle")
    n_vias   = sum(1 for c in board.vias_g.children if c.tag == "circle" and c.attrs.get("id", "").startswith("via-"))
    n_overlays = len(board.overlay_g.children)
    return 200, "application/json", _json({
        "board_w":    board.board_w,
        "board_h":    board.board_h,
        "layers":     list(board.layers.keys()),
        "pads":       n_pads,
        "tracks":     n_tracks,
        "vias":       n_vias,
        "overlays":   n_overlays,
    })


def handle_check_conflicts(board: BoardSVG) -> tuple[int, str, bytes]:
    """GET /svg/check — check all overlay polylines for segment intersections.

    Returns list of conflict points with the two trace IDs involved.
    Uses cross-product sign test for robust floating point intersection detection.
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")

    # Collect all segments from overlay polylines
    # Each entry: (trace_id, p1, p2)
    segments = []
    for el in board.overlay_g.children:
        if el.tag != "polyline":
            continue
        trace_id = el.attrs.get("id", "")
        points_str = el.attrs.get("points", "")
        if not points_str:
            continue
        pts = []
        for pair in points_str.strip().split():
            x, y = pair.split(",")
            pts.append((float(x), float(y)))
        for i in range(len(pts) - 1):
            segments.append((trace_id, pts[i], pts[i + 1]))

    # Check every segment pair for intersection
    conflicts = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            id_a, p1, p2 = segments[i]
            id_b, p3, p4 = segments[j]
            if id_a == id_b:
                continue  # skip segments of same trace
            pt = _segment_intersection(p1, p2, p3, p4)
            if pt:
                conflicts.append({
                    "trace_a": id_a,
                    "trace_b": id_b,
                    "point":   [pt[0], pt[1]],
                })

    return 200, "application/json", _json({
        "ok":       len(conflicts) == 0,
        "conflicts": conflicts,
        "count":    len(conflicts),
    })


# ── Internal helpers ──────────────────────────────────────────────────────────

def _element_summary(el: SVGElement) -> dict:
    """Compact JSON-serialisable summary of an SVGElement."""
    return {
        "tag":   el.tag,
        "id":    el.attrs.get("id", ""),
        "attrs": {k: v for k, v in el.attrs.items() if not k.startswith("_")},
    }

def _json(data: dict) -> bytes:
    return json.dumps(data).encode()

def _err(msg: str) -> bytes:
    return json.dumps({"ok": False, "error": msg}).encode()

def _cross(o, a, b) -> float:
    """2D cross product of vectors OA and OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

def _segment_intersection(p1, p2, p3, p4):
    """Return intersection point of segments p1-p2 and p3-p4, or None.

    Uses cross-product sign test — robust to floating point imprecision
    because only the sign matters, not the magnitude.
    """
    d1 = _cross(p3, p4, p1)
    d2 = _cross(p3, p4, p2)
    d3 = _cross(p1, p2, p3)
    d4 = _cross(p1, p2, p4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        # Segments intersect — compute the point
        denom = (p1[0]-p2[0])*(p3[1]-p4[1]) - (p1[1]-p2[1])*(p3[0]-p4[0])
        if denom == 0:
            return None
        t = ((p1[0]-p3[0])*(p3[1]-p4[1]) - (p1[1]-p3[1])*(p3[0]-p4[0])) / denom
        x = p1[0] + t * (p2[0] - p1[0])
        y = p1[1] + t * (p2[1] - p1[1])
        return (round(x, 3), round(y, 3))

    # Collinear overlap check
    if d1 == 0 and d2 == 0:
        # All four points collinear — check for overlap
        def on_seg(p, a, b):
            return min(a[0],b[0]) <= p[0] <= max(a[0],b[0]) and \
                   min(a[1],b[1]) <= p[1] <= max(a[1],b[1])
        if on_seg(p3, p1, p2): return p3
        if on_seg(p4, p1, p2): return p4
        if on_seg(p1, p3, p4): return p1

    return None