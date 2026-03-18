"""
courtyard.py
Checks for component body (courtyard) overlaps between footprints.

Approximates each footprint's courtyard as the bounding box of its pads
expanded by a per-footprint-type body margin. Warns when two footprints'
courtyards overlap.
"""

# Body margin (mm) added to pad bounding box on each side, keyed by substring
# of library:footprint. First match wins.
BODY_MARGINS = [
    ('C_Disc_D5.0mm',                          2.75),   # 5mm disc cap
    ('C_Disc_D4.3mm',                          2.25),
    ('C_Disc',                                 2.0),
    ('C_Radial',                               2.0),
    ('R_Axial_DIN0207_L6.3mm_D2.5mm',         1.5),
    ('R_Axial',                                1.5),
    ('DIP-8',                                  1.5),
    ('DIP-',                                   1.5),
]

DEFAULT_MARGIN = 1.5

COURTYARD_CLEARANCE = 0.25   # mm minimum gap between courtyards


def _margin_for(lib_fp):
    for key, margin in BODY_MARGINS:
        if key in lib_fp:
            return margin
    return DEFAULT_MARGIN


def _courtyard(ref, fp, pads_lib, pad_positions):
    """Return (x_min, y_min, x_max, y_max) courtyard for a footprint."""
    lib_fp = fp['lib']
    margin = _margin_for(lib_fp)

    # Collect all pad positions for this ref
    xs, ys = [], []
    for (r, p), (ax, ay, net) in pad_positions.items():
        if r == ref:
            xs.append(ax)
            ys.append(ay)

    if not xs:
        return None

    return (
        min(xs) - margin,
        min(ys) - margin,
        max(xs) + margin,
        max(ys) + margin,
    )


def _overlaps(a, b, clearance):
    """True if two (xmin,ymin,xmax,ymax) rectangles overlap including clearance."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return (ax1 < bx2 + clearance and ax2 > bx1 - clearance and
            ay1 < by2 + clearance and ay2 > by1 - clearance)


def run(tracks, pad_positions, nets, fps=None, pads_lib=None):
    if fps is None or pads_lib is None:
        return []

    # Compute courtyard for each footprint
    courts = {}
    for ref, fp in fps.items():
        c = _courtyard(ref, fp, pads_lib, pad_positions)
        if c:
            courts[ref] = c

    violations = []
    refs = sorted(courts.keys())
    for i, ref_a in enumerate(refs):
        for ref_b in refs[i+1:]:
            if _overlaps(courts[ref_a], courts[ref_b], COURTYARD_CLEARANCE):
                a = courts[ref_a]
                b = courts[ref_b]
                violations.append(
                    f"COURTYARD OVERLAP: {ref_a} [{a[0]:.2f},{a[1]:.2f} to {a[2]:.2f},{a[3]:.2f}]"
                    f"  vs  {ref_b} [{b[0]:.2f},{b[1]:.2f} to {b[2]:.2f},{b[3]:.2f}]"
                )

    return violations
