"""
crossings.py
Check 2: Same-layer, different-net trace crossings.

For every pair of track segments on the same layer with different nets:
check if they intersect. Currently handles orthogonal (H/V) crossings.
Diagonal traces require the general intersection test (TODO).
"""


def _h_v_cross(hx1, hy, hx2, vx, vy1, vy2, tol=1e-6):
    """True if horizontal segment (hx1,hy)-(hx2,hy) crosses vertical (vx,vy1)-(vx,vy2).
    Endpoints touching are NOT counted as crossings (they share a junction point).
    """
    h_lo, h_hi = min(hx1, hx2), max(hx1, hx2)
    v_lo, v_hi = min(vy1, vy2), max(vy1, vy2)
    # Strict interior crossing only
    return (h_lo + tol < vx < h_hi - tol) and (v_lo + tol < hy < v_hi - tol)


def _segments_cross(x1, y1, x2, y2, x3, y3, x4, y4, tol=1e-6):
    """Check crossing between two orthogonal segments."""
    h1 = abs(y1 - y2) < tol   # seg1 horizontal
    h2 = abs(y3 - y4) < tol   # seg2 horizontal

    if h1 and not h2:   # seg1 H, seg2 V
        return _h_v_cross(x1, y1, x2, x3, y3, y4, tol)
    if h2 and not h1:   # seg2 H, seg1 V
        return _h_v_cross(x3, y3, x4, x1, y1, y2, tol)
    # Both H or both V — handled by collinear check, not here
    return False


def run(tracks, pad_positions, nets):
    """
    Args:
        tracks        : [(x1,y1,x2,y2,width,layer,net), ...]
        pad_positions : unused, kept for uniform signature
        nets          : unused, kept for uniform signature

    Returns:
        List of violation strings (empty = PASS).
    """
    violations = []
    n = len(tracks)

    for i in range(n):
        for j in range(i + 1, n):
            t1 = tracks[i]
            t2 = tracks[j]
            if t1[5] != t2[5]:   # different layers — OK
                continue
            if t1[6] == t2[6]:   # same net — OK
                continue
            if _segments_cross(t1[0], t1[1], t1[2], t1[3],
                                t2[0], t2[1], t2[2], t2[3]):
                violations.append(
                    f"CROSSING [{t1[5]}]: "
                    f"{t1[6]} ({t1[0]},{t1[1]})->({t1[2]},{t1[3]})  X  "
                    f"{t2[6]} ({t2[0]},{t2[1]})->({t2[2]},{t2[3]})"
                )

    return violations
