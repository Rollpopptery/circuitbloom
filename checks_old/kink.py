"""
kink.py
Detects routing kinks — consecutive segments on the same net that form
unnecessary detours.

A kink is defined as three connected collinear segments A→B→C→D where:
- A→B is horizontal/vertical
- B→C is perpendicular (the jog)
- C→D is the same direction as A→B
This can always be simplified to two segments.

Also detects U-turns: two connected segments where the endpoint is closer
to the start than the shared midpoint, meaning the route doubles back.
"""


def _build_net_segments(tracks):
    """Group track segments by net. Returns {net: [(x1,y1,x2,y2), ...]}"""
    net_segs = {}
    for (x1, y1, x2, y2, w, layer, net) in tracks:
        net_segs.setdefault((net, layer), []).append((x1, y1, x2, y2))
    return net_segs


def _connected(seg_a, seg_b, tol=1e-6):
    """Return the shared endpoint if two segments share one, else None."""
    ax1, ay1, ax2, ay2 = seg_a
    bx1, by1, bx2, by2 = seg_b
    endpoints_a = [(ax1, ay1), (ax2, ay2)]
    endpoints_b = [(bx1, by1), (bx2, by2)]
    for pa in endpoints_a:
        for pb in endpoints_b:
            if abs(pa[0] - pb[0]) < tol and abs(pa[1] - pb[1]) < tol:
                return pa
    return None


def _direction(x1, y1, x2, y2):
    """Return 'H' for horizontal, 'V' for vertical, or None."""
    if abs(y2 - y1) < 1e-6:
        return 'H'
    if abs(x2 - x1) < 1e-6:
        return 'V'
    return None


def _other_end(seg, shared):
    """Return the endpoint of seg that is NOT the shared point."""
    x1, y1, x2, y2 = seg
    if abs(x1 - shared[0]) < 1e-6 and abs(y1 - shared[1]) < 1e-6:
        return (x2, y2)
    return (x1, y1)


def _manhattan(p1, p2):
    return abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])


def run(tracks, pad_positions, nets):
    hints = []
    net_segs = _build_net_segments(tracks)

    for (net, layer), segs in net_segs.items():
        n = len(segs)
        for i in range(n):
            for j in range(i + 1, n):
                shared = _connected(segs[i], segs[j])
                if shared is None:
                    continue

                a_start = _other_end(segs[i], shared)
                b_end   = _other_end(segs[j], shared)

                dir_a = _direction(a_start[0], a_start[1], shared[0], shared[1])
                dir_b = _direction(shared[0], shared[1], b_end[0], b_end[1])

                if dir_a is None or dir_b is None:
                    continue

                # U-turn: endpoint closer to start than the jog midpoint
                if _manhattan(a_start, b_end) < _manhattan(a_start, shared):
                    saving = _manhattan(a_start, shared) + _manhattan(shared, b_end) - _manhattan(a_start, b_end)
                    hints.append(
                        f"KINK [{layer}] net={net}: "
                        f"({a_start[0]:.2f},{a_start[1]:.2f})->({shared[0]:.2f},{shared[1]:.2f})->({b_end[0]:.2f},{b_end[1]:.2f}) "
                        f"doubles back, saves {saving:.2f}mm if simplified"
                    )

                # Same-direction jog: A→B and C→D parallel, joined by perpendicular B→C
                # Check for a third segment continuing in same direction as A
                elif dir_a == dir_b:
                    saving = _manhattan(a_start, shared) + _manhattan(shared, b_end) - _manhattan(a_start, b_end)
                    if saving > 1e-6:
                        hints.append(
                            f"KINK [{layer}] net={net}: "
                            f"({a_start[0]:.2f},{a_start[1]:.2f})->({shared[0]:.2f},{shared[1]:.2f})->({b_end[0]:.2f},{b_end[1]:.2f}) "
                            f"same-direction jog, saves {saving:.2f}mm if straightened"
                        )

    return hints
