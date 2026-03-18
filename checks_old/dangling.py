"""
dangling.py
Check 5: Dangling segment endpoints.

A segment endpoint is dangling if it has no connection to anything on the
same net within tolerance:
  - no pad at that position, AND
  - no other segment endpoint at that position, AND
  - no mid-segment touch on another segment of the same net

Both endpoints of every segment are checked. If either floats free,
the segment is a stub going nowhere and is reported as a violation.

Exception (warning, not error):
  If a dangling endpoint is within BOARD_EDGE_TOL of the board edge it is
  flagged as a WARNING (possible intentional test-point stub) rather than
  a hard FAIL. Board dimensions are optional — if not supplied the exception
  is not applied.
"""

import math

DEFAULT_TOL           = 0.01   # mm — point-match tolerance
DEFAULT_BOARD_EDGE_TOL = 0.5   # mm — within this of board edge -> warn not fail
DEFAULT_SEG_TOL       = 0.01   # mm — tolerance for mid-segment touch


def _near(ax, ay, bx, by, tol):
    return math.hypot(ax - bx, ay - by) < tol


def _point_on_segment(px, py, x1, y1, x2, y2, tol):
    """
    Return True if point (px,py) lies on the line segment (x1,y1)-(x2,y2)
    within tol mm.  Handles both horizontal and vertical segments only
    (our routing is orthogonal), with a small general-case fallback.
    """
    # Check bounding box first (fast reject)
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    if not (min_x - tol <= px <= max_x + tol and
            min_y - tol <= py <= max_y + tol):
        return False
    # Distance from point to infinite line
    seg_len = math.hypot(x2 - x1, y2 - y1)
    if seg_len < tol:
        return _near(px, py, x1, y1, tol)
    cross = abs((x2 - x1) * (y1 - py) - (x1 - px) * (y2 - y1)) / seg_len
    return cross < tol


def _near_board_edge(px, py, board_w, board_h, edge_tol):
    """Return True if point is within edge_tol of any board edge."""
    return (px < edge_tol or px > board_w - edge_tol or
            py < edge_tol or py > board_h - edge_tol)


def run(tracks, pad_positions, nets,
        tol=DEFAULT_TOL,
        seg_tol=DEFAULT_SEG_TOL,
        board_w=None, board_h=None,
        board_edge_tol=DEFAULT_BOARD_EDGE_TOL):
    """
    Args:
        tracks        : [(x1,y1,x2,y2,width,layer,net), ...]
        pad_positions : {(ref,pad): (abs_x, abs_y, net_name)}
        nets          : unused, kept for uniform signature
        tol           : endpoint coincidence tolerance (mm)
        seg_tol       : mid-segment touch tolerance (mm)
        board_w/h     : board dimensions (mm) for edge exception
        board_edge_tol: distance from edge to treat as warning not error

    Returns:
        List of violation strings (empty = PASS).
        Warnings are prefixed with WARNING: rather than DANGLING:.
    """
    violations = []

    # Build pad lookup by net: net_name -> [(px, py), ...]
    pads_by_net = {}
    for (ref, pnum), (px, py, net_name) in pad_positions.items():
        pads_by_net.setdefault(net_name, []).append((px, py))

    # Build segment endpoint lookup by net: net_name -> [(x,y), ...]
    endpoints_by_net = {}
    for (x1, y1, x2, y2, w, layer, net) in tracks:
        endpoints_by_net.setdefault(net, []).append((x1, y1))
        endpoints_by_net.setdefault(net, []).append((x2, y2))

    for seg in tracks:
        x1, y1, x2, y2, w, layer, net = seg

        for (px, py, label) in [(x1, y1, "start"), (x2, y2, "end")]:

            # 1. Does it land on a pad of the same net?
            on_pad = any(
                _near(px, py, padx, pady, tol)
                for padx, pady in pads_by_net.get(net, [])
            )
            if on_pad:
                continue

            # 2. Does it coincide with another segment endpoint on same net?
            on_endpoint = sum(
                1 for ex, ey in endpoints_by_net.get(net, [])
                if _near(px, py, ex, ey, tol)
            ) > 1  # >1 because the point matches itself
            if on_endpoint:
                continue

            # 3. Does it land on the mid-body of another segment on same net?
            on_midseg = any(
                _point_on_segment(px, py, sx1, sy1, sx2, sy2, seg_tol)
                for (sx1, sy1, sx2, sy2, sw, slayer, snet) in tracks
                if snet == net and (sx1, sy1, sx2, sy2) != (x1, y1, x2, y2)
            )
            if on_midseg:
                continue

            # Endpoint is dangling — apply board-edge exception
            msg = (
                f"({px:.3f},{py:.3f}) {label} of segment "
                f"({x1:.3f},{y1:.3f})->({x2:.3f},{y2:.3f}) "
                f"layer={layer} net={net}"
            )

            if (board_w is not None and board_h is not None and
                    _near_board_edge(px, py, board_w, board_h, board_edge_tol)):
                violations.append(f"WARNING dangling near board edge: {msg}")
            else:
                violations.append(f"DANGLING: {msg}")

    return violations