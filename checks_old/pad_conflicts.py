"""
pad_conflicts.py
Check 1: Trace-vs-pad distance check.

For every track segment, for every through-hole pad on a DIFFERENT net:
compute minimum distance from pad centre to segment. If distance < pad_radius
+ clearance, it's a violation.

Through-hole pads exist on ALL copper layers — B.Cu traces are checked
against all TH pads, same as F.Cu traces.

Default values match standard 0.8mm-drill TH pads with 0.2mm clearance:
    hit_distance = 0.8 (pad_radius) + 0.2 (clearance) = 1.0mm
"""

from .dpcb_parser import pt_seg_dist

# Default design rules — can be overridden via run() kwargs
DEFAULT_PAD_RADIUS = 0.8   # mm  (1.6mm diameter TH pad)
DEFAULT_CLEARANCE  = 0.2   # mm


def run(tracks, pad_positions, nets,
        pad_radius=DEFAULT_PAD_RADIUS,
        clearance=DEFAULT_CLEARANCE):
    """
    Args:
        tracks        : [(x1,y1,x2,y2,width,layer,net), ...]
        pad_positions : {(ref,pad): (abs_x, abs_y, net_name)}
        nets          : unused here, kept for uniform signature
        pad_radius    : radius of TH pad copper (mm)
        clearance     : minimum clearance (mm)

    Returns:
        List of violation strings (empty = PASS).
    """
    hit_dist = pad_radius + clearance
    violations = []

    for (ref, pnum), (px, py, pad_net) in pad_positions.items():
        for (x1, y1, x2, y2, w, layer, trk_net) in tracks:
            if trk_net == pad_net:
                continue
            d = pt_seg_dist(px, py, x1, y1, x2, y2)
            if d < hit_dist:
                violations.append(
                    f"PAD CONFLICT: {ref}.{pnum}({pad_net}) dist={d:.3f}mm "
                    f"< {hit_dist}mm  vs  TRK:{trk_net} [{layer}] "
                    f"({x1},{y1})->({x2},{y2})"
                )

    return violations
