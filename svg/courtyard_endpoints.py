#!/usr/bin/env python3
"""
courtyard_endpoints.py — HTTP handlers for /courtyards/* endpoints.

Computes per-component bounding box courtyards from pad positions and
draws them as translucent grey rectangles into the overlays group.
Also provides courtyard data for DSN keepout export.

No HTTP server knowledge. No Shapely dependency.
"""

import json
from board_svg import BoardSVG


def _pads_by_ref(board: BoardSVG) -> dict[str, list[dict]]:
    """Group pad elements by component ref."""
    groups: dict[str, list[dict]] = {}
    for el in board.pads_g.children:
        if el.tag != 'circle':
            continue
        ref = el.attrs.get('data-ref', '')
        if not ref:
            continue
        groups.setdefault(ref, []).append({
            'ref': ref,
            'pin': el.attrs.get('data-pin', ''),
            'x':   float(el.attrs.get('cx', 0)),
            'y':   float(el.attrs.get('cy', 0)),
        })
    return groups


def _cluster_pads(pads: list[dict], threshold: float = 2.0) -> list[list[dict]]:
    """
    Group pads into clusters where each pad is within threshold mm
    of at least one other pad in the same cluster.
    Uses single-linkage clustering.
    """
    if not pads:
        return []

    clusters: list[list[dict]] = []

    for pad in pads:
        # Find all existing clusters that have a pad within threshold
        merged = []
        for i, cluster in enumerate(clusters):
            for cp in cluster:
                dx = pad['x'] - cp['x']
                dy = pad['y'] - cp['y']
                if (dx*dx + dy*dy) <= threshold * threshold:
                    merged.append(i)
                    break

        if not merged:
            clusters.append([pad])
        elif len(merged) == 1:
            clusters[merged[0]].append(pad)
        else:
            # Merge all matching clusters together
            new_cluster = [pad]
            for i in sorted(merged, reverse=True):
                new_cluster.extend(clusters.pop(i))
            clusters.append(new_cluster)

    return clusters


def compute_courtyards(board: BoardSVG,
                       margin: float = 0.5,
                       cluster_threshold: float = 2.0) -> list[dict]:
    """
    Compute courtyard rectangles per component, one per pad cluster.

    Pads within cluster_threshold mm of each other are grouped into the
    same courtyard. This produces tight strip courtyards for pin headers
    rather than one large box spanning the whole component.

    Args:
        board:             BoardSVG instance
        margin:            padding around each cluster bounding box in mm
        cluster_threshold: max distance between pads in the same cluster

    Returns:
        List of dicts with ref, cluster, x, y, w, h (all in mm).
    """
    courtyards = []
    for ref, pads in _pads_by_ref(board).items():
        clusters = _cluster_pads(pads, threshold=cluster_threshold)
        for ci, cluster in enumerate(clusters):
            xs = [p['x'] for p in cluster]
            ys = [p['y'] for p in cluster]
            x1 = round(min(xs) - margin, 4)
            y1 = round(min(ys) - margin, 4)
            x2 = round(max(xs) + margin, 4)
            y2 = round(max(ys) + margin, 4)
            courtyards.append({
                'ref':     ref,
                'cluster': ci,
                'x':       x1,
                'y':       y1,
                'w':       round(x2 - x1, 4),
                'h':       round(y2 - y1, 4),
            })
    return courtyards


def handle_courtyards_draw(board: BoardSVG,
                           margin: float = 0.5) -> tuple[int, str, bytes]:
    """
    GET /courtyards/draw?margin=0.5

    Compute per-component courtyards and add translucent grey rectangles
    into the overlays group.
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")

    courtyards = compute_courtyards(board, margin)

    for c in courtyards:
        board.add_element("rect", {
            "id":           f"courtyard-{c['ref']}-{c['cluster']}",
            "x":            str(c['x']),
            "y":            str(c['y']),
            "width":        str(c['w']),
            "height":       str(c['h']),
            "fill":         "#88888822",
            "stroke":       "#888888",
            "stroke-width": "0.05",
            "stroke-dasharray": "0.3,0.2",
            "data-ref":     c['ref'],
        })

    return 200, "application/json", _json({
        "ok":    True,
        "count": len(courtyards),
        "margin": margin,
    })


def handle_courtyards_json(board: BoardSVG,
                           margin: float = 0.5) -> tuple[int, str, bytes]:
    """
    GET /courtyards?margin=0.5

    Return courtyard bounding boxes as JSON without drawing them.
    Used by svg_to_dsn to generate DSN keepout regions.
    """
    if board is None:
        return 404, "application/json", _err("no board loaded")

    courtyards = compute_courtyards(board, margin)
    return 200, "application/json", _json({
        "ok":        True,
        "count":     len(courtyards),
        "margin":    margin,
        "courtyards": courtyards,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(data: dict) -> bytes:
    return json.dumps(data).encode()

def _err(msg: str) -> bytes:
    return json.dumps({"ok": False, "error": msg}).encode()