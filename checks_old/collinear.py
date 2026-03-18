"""
collinear.py
Check 3: Collinear conflict — two different-net traces running along the
same axis with overlapping ranges. This is an invisible short circuit.

Examples:
    VCC  horizontal at y=76, x=[125, 152]
    GND  horizontal at y=76, x=[130, 140]   ← overlap → short

    THRES vertical at x=135, y=[80, 85]
    GND   vertical at x=135, y=[83, 90]     ← overlap → short
"""

TOL = 1e-6


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

            x1, y1, x2, y2 = t1[:4]
            x3, y3, x4, y4 = t2[:4]

            # Both horizontal, same y
            if (abs(y1 - y2) < TOL and abs(y3 - y4) < TOL
                    and abs(y1 - y3) < TOL):
                lo1, hi1 = min(x1, x2), max(x1, x2)
                lo2, hi2 = min(x3, x4), max(x3, x4)
                overlap = min(hi1, hi2) - max(lo1, lo2)
                if overlap > TOL:
                    violations.append(
                        f"COLLINEAR H [{t1[5]}] y={y1:.3f}: "
                        f"{t1[6]} x=[{lo1},{hi1}]  overlaps  "
                        f"{t2[6]} x=[{lo2},{hi2}]  (overlap={overlap:.3f}mm)"
                    )

            # Both vertical, same x
            if (abs(x1 - x2) < TOL and abs(x3 - x4) < TOL
                    and abs(x1 - x3) < TOL):
                lo1, hi1 = min(y1, y2), max(y1, y2)
                lo2, hi2 = min(y3, y4), max(y3, y4)
                overlap = min(hi1, hi2) - max(lo1, lo2)
                if overlap > TOL:
                    violations.append(
                        f"COLLINEAR V [{t1[5]}] x={x1:.3f}: "
                        f"{t1[6]} y=[{lo1},{hi1}]  overlaps  "
                        f"{t2[6]} y=[{lo2},{hi2}]  (overlap={overlap:.3f}mm)"
                    )

    return violations
