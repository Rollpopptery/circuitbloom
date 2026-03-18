"""
orphaned_vias.py
Detects vias that are not connected to tracks on both layers.

A via is:
- ORPHANED: no track endpoints at its position on any layer
- DANGLING: track endpoints on only one layer (not acting as a layer transition)
"""

TOLERANCE = 0.01  # mm


def _tracks_at(x, y, layer, tracks):
    """Count track endpoints at (x, y) on given layer."""
    count = 0
    for (x1, y1, x2, y2, w, lyr, net) in tracks:
        if lyr != layer:
            continue
        if (abs(x1 - x) < TOLERANCE and abs(y1 - y) < TOLERANCE) or \
           (abs(x2 - x) < TOLERANCE and abs(y2 - y) < TOLERANCE):
            count += 1
    return count


def run(tracks, pad_positions, nets, vias=None):
    if not vias:
        return []

    violations = []
    for (x, y, drill, annular, net) in vias:
        f_count = _tracks_at(x, y, 'F.Cu', tracks)
        b_count = _tracks_at(x, y, 'B.Cu', tracks)

        if f_count == 0 and b_count == 0:
            violations.append(
                f"ORPHANED VIA: ({x},{y}) net={net} — no tracks on either layer"
            )
        elif f_count == 0:
            violations.append(
                f"DANGLING VIA: ({x},{y}) net={net} — no F.Cu tracks (only B.Cu)"
            )
        elif b_count == 0:
            violations.append(
                f"DANGLING VIA: ({x},{y}) net={net} — no B.Cu tracks (only F.Cu)"
            )

    return violations
