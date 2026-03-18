"""
mitre.py
Iterative corner mitring tool.

Finds the two orthogonal segments meeting at a given corner point on a
specific net+layer, then grows a 45-degree mitre segment incrementally
until the next step would introduce a pad conflict or crossing violation.
Returns the track list with the corner mitred at the maximum clean size.

The diagonal segment is checked using a general segment-intersection test
since crossings.py only handles orthogonal pairs.

Usage:
    from checks.mitre import mitre_corner
    from checks.dpcb_parser import parse_dpcb, compute_pad_positions

    fps, pads_lib, nets, tracks = parse_dpcb('design.dpcb')
    pad_positions, _ = compute_pad_positions(fps, pads_lib, nets)

    tracks = mitre_corner(tracks, pad_positions, nets,
                          corner=(52, 10), net='GND', layer='F.Cu')
    tracks = mitre_corner(tracks, pad_positions, nets,
                          corner=(8, 27.08), net='/OUT', layer='B.Cu')
"""

import math
from .pad_conflicts import run as check_pads

DEFAULT_STEP    = 3.0   # mm — increment per iteration
DEFAULT_MIN     = 0.5   # mm — minimum worthwhile mitre
DEFAULT_MAX     = 6.0   # mm — hard ceiling (AI intent, not physics)
DEFAULT_TOL     = 0.01  # mm — coordinate match tolerance


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _near(ax, ay, bx, by, tol=DEFAULT_TOL):
    return math.hypot(ax - bx, ay - by) < tol


def _seg_len(x1, y1, x2, y2):
    return math.hypot(x2 - x1, y2 - y1)


def _seg_intersects(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2, tol=1e-6):
    """
    General 2D segment intersection test (no collinear handling needed here).
    Returns True if the two segments properly intersect (not just touch at endpoints).
    """
    def cross2d(ox, oy, ax, ay, bx, by):
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

    d1 = cross2d(bx1, by1, bx2, by2, ax1, ay1)
    d2 = cross2d(bx1, by1, bx2, by2, ax2, ay2)
    d3 = cross2d(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = cross2d(ax1, ay1, ax2, ay2, bx2, by2)

    if ((d1 > tol and d2 < -tol) or (d1 < -tol and d2 > tol)) and \
       ((d3 > tol and d4 < -tol) or (d3 < -tol and d4 > tol)):
        return True
    return False


def _diagonal_clear(dx1, dy1, dx2, dy2, diag_net, diag_layer,
                    tracks, pad_positions, tol=DEFAULT_TOL):
    """
    Check that a proposed diagonal segment (dx1,dy1)->(dx2,dy2) does not:
    1. Cross any existing segment of a different net on the same layer
    2. Come within 1.0mm of any TH pad on a different net (pad conflict)

    Returns list of violation strings (empty = clear).
    """
    violations = []

    # --- Crossing check: diagonal vs all existing segments ---
    for (x1, y1, x2, y2, w, layer, net) in tracks:
        if layer != diag_layer:
            continue
        if net == diag_net:
            continue
        if _seg_intersects(dx1, dy1, dx2, dy2, x1, y1, x2, y2, tol):
            violations.append(
                f"CROSSING [{diag_layer}]: {diag_net} diagonal "
                f"({dx1:.3f},{dy1:.3f})->({dx2:.3f},{dy2:.3f})  X  "
                f"{net} ({x1},{y1})->({x2},{y2})"
            )

    # --- Pad conflict: diagonal vs all TH pads on different nets ---
    # Reuse pad_conflicts logic: minimum distance from pad centre to segment
    for (ref, pnum), (px, py, pad_net) in pad_positions.items():
        if pad_net == diag_net:
            continue
        # Distance from pad centre to diagonal segment
        seg_len = _seg_len(dx1, dy1, dx2, dy2)
        if seg_len < tol:
            dist = math.hypot(px - dx1, py - dy1)
        else:
            # Project pad onto segment line
            t = ((px - dx1) * (dx2 - dx1) + (py - dy1) * (dy2 - dy1)) / (seg_len ** 2)
            t = max(0.0, min(1.0, t))
            cx = dx1 + t * (dx2 - dx1)
            cy = dy1 + t * (dy2 - dy1)
            dist = math.hypot(px - cx, py - cy)

        hit_dist = 0.8 + 0.2  # pad_radius + clearance
        if dist < hit_dist:
            violations.append(
                f"PAD CONFLICT: {ref}.{pnum}({pad_net}) dist={dist:.3f}mm < {hit_dist}mm"
                f"  vs  diagonal {diag_net} [{diag_layer}]"
            )

    return violations


# ---------------------------------------------------------------------------
# Apply mitre to track list
# ---------------------------------------------------------------------------

def _apply_mitre(tracks, corner, net, layer, seg_a, seg_b, size, tol=DEFAULT_TOL):
    """
    Replace seg_a and seg_b in tracks with shortened versions plus a
    45-degree diagonal of the given size.

    seg_a and seg_b are the original full-length segments. The corner is
    their shared endpoint. size is the chamfer distance along each leg.

    Returns (new_tracks, diag_start, diag_end) or None if geometry fails.
    """
    cx, cy = corner
    (ax1, ay1, ax2, ay2, aw, al, an) = seg_a
    (bx1, by1, bx2, by2, bw, bl, bn) = seg_b

    # Find which end of seg_a is the corner
    if _near(ax2, ay2, cx, cy, tol):
        a_far = (ax1, ay1)
        a_corner_end = (ax2, ay2)
    else:
        a_far = (ax2, ay2)
        a_corner_end = (ax1, ay1)

    # Find which end of seg_b is the corner
    if _near(bx1, by1, cx, cy, tol):
        b_far = (bx2, by2)
    else:
        b_far = (bx1, by1)

    # Point on leg A at 'size' distance from corner (toward a_far)
    la = _seg_len(cx, cy, a_far[0], a_far[1])
    lb = _seg_len(cx, cy, b_far[0], b_far[1])

    if la < size - tol or lb < size - tol:
        return None  # size exceeds leg length

    p1x = cx + (a_far[0] - cx) / la * size
    p1y = cy + (a_far[1] - cy) / la * size
    p2x = cx + (b_far[0] - cx) / lb * size
    p2y = cy + (b_far[1] - cy) / lb * size

    # Rebuild shortened seg_a: a_far -> p1
    new_a = (a_far[0], a_far[1], p1x, p1y, aw, al, an)
    # Rebuild shortened seg_b: p2 -> b_far
    new_b = (p2x, p2y, b_far[0], b_far[1], bw, bl, bn)
    # Diagonal: p1 -> p2
    diag_w = min(aw, bw)  # use narrower of the two widths
    diag = (p1x, p1y, p2x, p2y, diag_w, layer, net)

    # Replace original segments in track list.
    # Only seg_a and seg_b are touched — T-junctions are rejected upstream.
    new_tracks = []
    for t in tracks:
        if t is seg_a or t is seg_b:
            continue
        new_tracks.append(t)
    new_tracks.append(new_a)
    new_tracks.append(new_b)
    new_tracks.append(diag)

    return new_tracks, (p1x, p1y), (p2x, p2y)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def mitre_corner(tracks, pad_positions, nets,
                 corner, net, layer,
                 step=DEFAULT_STEP,
                 min_size=DEFAULT_MIN,
                 max_size=DEFAULT_MAX,
                 tol=DEFAULT_TOL):
    """
    Grow a 45-degree mitre at `corner` on `net`/`layer` until the next
    step would introduce a violation. Returns the modified track list.

    Args:
        tracks        : [(x1,y1,x2,y2,width,layer,net), ...]
        pad_positions : {(ref,pad): (abs_x, abs_y, net_name)}
        nets          : net dict (unused here, passed for consistency)
        corner        : (x, y) of the corner to mitre
        net           : net name string
        layer         : layer string e.g. 'F.Cu'
        step          : increment size in mm (default 0.1)
        min_size      : minimum mitre to bother applying (default 0.5mm)
        max_size      : hard ceiling on mitre size (default 10mm)

    Returns:
        (tracks, result_dict) where result_dict contains:
            'size'     : final mitre size applied (0 if none)
            'reason'   : why growth stopped
            'diag'     : (start, end) of diagonal, or None
    """
    cx, cy = corner

    # --- Find the two segments meeting at this corner ---
    meeting = []
    for t in tracks:
        x1, y1, x2, y2, w, lyr, n = t
        if lyr != layer or n != net:
            continue
        at_start = _near(x1, y1, cx, cy, tol)
        at_end   = _near(x2, y2, cx, cy, tol)
        if at_start or at_end:
            meeting.append(t)

    if len(meeting) < 2:
        return tracks, {
            'size': 0, 'diag': None,
            'reason': f"Corner ({cx},{cy}) net={net} layer={layer}: "
                      f"found {len(meeting)} segment(s), need 2"
        }

    if len(meeting) > 2:
        return tracks, {
            'size': 0, 'diag': None,
            'reason': f"Corner ({cx},{cy}): T-junction ({len(meeting)} segments) — skip"
        }

    seg_a, seg_b = meeting[0], meeting[1]

    # --- Verify they actually form a 90° corner ---
    ha = abs(seg_a[1] - seg_a[3]) < tol
    hb = abs(seg_b[1] - seg_b[3]) < tol
    va = abs(seg_a[0] - seg_a[2]) < tol
    vb = abs(seg_b[0] - seg_b[2]) < tol
    if not ((ha and vb) or (va and hb)):
        return tracks, {
            'size': 0, 'diag': None,
            'reason': f"Corner ({cx},{cy}): segments are not orthogonal"
        }

    # --- Maximum possible size from leg lengths ---
    cx2, cy2 = corner
    def far_end(seg):
        x1,y1,x2,y2 = seg[0],seg[1],seg[2],seg[3]
        if _near(x2,y2,cx2,cy2,tol):
            return (x1,y1)
        return (x2,y2)

    a_far = far_end(seg_a)
    b_far = far_end(seg_b)
    max_from_legs = min(
        _seg_len(cx, cy, a_far[0], a_far[1]),
        _seg_len(cx, cy, b_far[0], b_far[1])
    )
    effective_max = min(max_size, max_from_legs)

    if effective_max < min_size:
        return tracks, {
            'size': 0, 'diag': None,
            'reason': f"Corner ({cx},{cy}): max possible {effective_max:.2f}mm < min {min_size}mm"
        }

    # --- Grow iteratively ---
    # Use caller's step, but cap it so short legs still get at least one increment.
    # e.g. a 1.5mm leg with step=3.0 would immediately overshoot — use 40% of
    # the shorter leg as the effective step if that's smaller than the requested step.
    effective_step = min(step, effective_max * 0.4)
    effective_step = max(effective_step, 0.1)  # never finer than 0.1mm

    best_tracks = tracks
    best_size   = 0
    best_diag   = None
    reason      = "reached max size"

    size = effective_step
    while size <= effective_max + tol:
        result = _apply_mitre(tracks, corner, net, layer, seg_a, seg_b, size, tol)
        if result is None:
            reason = f"leg length exceeded at {size:.2f}mm"
            break

        candidate_tracks, d_start, d_end = result

        # Check the diagonal for violations
        viols = _diagonal_clear(d_start[0], d_start[1], d_end[0], d_end[1],
                                net, layer, tracks, pad_positions, tol)

        if viols:
            reason = f"violation at {size:.2f}mm: {viols[0]}"
            break

        # This size is clean — record it
        best_tracks = candidate_tracks
        best_size   = size
        best_diag   = (d_start, d_end)
        size        = round(size + effective_step, 6)

    if best_size < min_size:
        return tracks, {
            'size': 0, 'diag': None,
            'reason': f"max clean size {best_size:.2f}mm < min {min_size}mm — not applied"
        }

    return best_tracks, {
        'size':   best_size,
        'diag':   best_diag,
        'reason': reason
    }