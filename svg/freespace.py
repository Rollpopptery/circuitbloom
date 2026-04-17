#!/usr/bin/env python3
"""
freespace.py — Free space geometry for PCB routing.

Computes the navigable area of a board by subtracting pad keepout boxes
from the board outline. Provides corner detection and line-sweep utilities
for routing waypoint generation.

No HTTP, no BoardSVG dependency — pure Shapely geometry.
"""

from __future__ import annotations
from shapely.geometry import box, LineString, MultiPolygon, Polygon
from shapely.ops import unary_union


def compute_freespace(pads: list[dict], board_w: float, board_h: float,
                      clearance: float,
                      traces: list[dict] = None) -> Polygon | MultiPolygon:
    """
    Subtract pad keepout boxes and trace buffers from the board rectangle.

    Args:
        pads:      list of {"x": float, "y": float}
        board_w:   board width in mm
        board_h:   board height in mm
        clearance: half-size of keepout box around each pad centre,
                   and buffer radius around each trace segment
        traces:    list of {"x1", "y1", "x2", "y2"} trace segments,
                   or None to ignore traces

    Returns:
        Shapely Polygon or MultiPolygon representing navigable space.
    """
    obstacle_list = []

    for p in (pads or []):
        obstacle_list.append(
            box(p["x"] - clearance, p["y"] - clearance,
                p["x"] + clearance, p["y"] + clearance)
        )

    for t in (traces or []):
        line = LineString([(t["x1"], t["y1"]), (t["x2"], t["y2"])])
        obstacle_list.append(line.buffer(clearance, cap_style=2))

    if not obstacle_list:
        return box(0, 0, board_w, board_h)

    obstacles = unary_union(obstacle_list)
    return box(0, 0, board_w, board_h).difference(obstacles)


def ring_to_path(coords) -> str:
    """Convert a ring of (x, y) coordinates to an SVG path string."""
    pts = list(coords)
    d = f"M {pts[0][0]} {pts[0][1]}"
    for x, y in pts[1:]:
        d += f" L {x} {y}"
    return d + " Z"


def polygon_to_path(poly: Polygon) -> str:
    """Convert a Shapely Polygon (with holes) to an SVG path string."""
    d = ring_to_path(poly.exterior.coords)
    for interior in poly.interiors:
        d += " " + ring_to_path(interior.coords)
    return d


def find_corners(ring) -> list[tuple]:
    """
    Find all true 90-degree corners in a ring.

    Returns list of (x, y, ext) where ext is ('h', x, y) or ('v', x, y)
    indicating the direction a routing line should extend from that corner.
    """
    pts = list(ring.coords)[:-1]
    corners = []
    n = len(pts)
    for i in range(n):
        prev = pts[(i - 1) % n]
        curr = pts[i]
        nxt  = pts[(i + 1) % n]
        dx1 = round(curr[0] - prev[0], 3)
        dy1 = round(curr[1] - prev[1], 3)
        dx2 = round(nxt[0]  - curr[0], 3)
        dy2 = round(nxt[1]  - curr[1], 3)
        if ((dx1 != 0 and dy1 == 0 and dx2 == 0 and dy2 != 0) or
                (dx1 == 0 and dy1 != 0 and dx2 != 0 and dy2 == 0)):
            if dx1 != 0:
                ext = ('v', round(curr[0], 3), round(curr[1], 3))
            else:
                ext = ('h', round(curr[0], 3), round(curr[1], 3))
            corners.append((round(curr[0], 3), round(curr[1], 3), ext))
    return corners


def all_corners(poly: Polygon) -> list[tuple]:
    """Return all 90-degree corners from exterior and all interior rings."""
    result = find_corners(poly.exterior)
    for ring in poly.interiors:
        result += find_corners(ring)
    return result


def sweep_lines(corners: list[tuple], free_space: Polygon,
                board_w: float, board_h: float) -> tuple[list, list, list]:
    """
    Shoot horizontal/vertical lines from each corner, clipped to free space.

    Returns:
        (segments, h_lines, v_lines) where segments is a list of
        {"x1", "y1", "x2", "y2"} dicts and h_lines/v_lines are the
        Shapely LineString segments for crossing detection.
    """
    segments = []
    h_lines  = []
    v_lines  = []

    for x, y, ext in corners:
        if ext[0] == 'h':
            line = LineString([(0, y), (board_w, y)])
        else:
            line = LineString([(x, 0), (x, board_h)])

        clipped = line.intersection(free_space)
        geoms = list(clipped.geoms) if hasattr(clipped, 'geoms') else [clipped]

        for seg in geoms:
            if seg.is_empty or seg.length < 0.1:
                continue
            c = list(seg.coords)
            segments.append({
                "x1": round(c[0][0],  3), "y1": round(c[0][1],  3),
                "x2": round(c[-1][0], 3), "y2": round(c[-1][1], 3),
            })
            if ext[0] == 'h':
                h_lines.append(seg)
            else:
                v_lines.append(seg)

    return segments, h_lines, v_lines


def find_crossings(h_lines: list, v_lines: list) -> list[tuple]:
    """Return all (x, y) intersection points between h and v line sets."""
    crossings = set()
    for hl in h_lines:
        for vl in v_lines:
            pt = hl.intersection(vl)
            if not pt.is_empty:
                crossings.add((round(pt.x, 3), round(pt.y, 3)))
    return list(crossings)