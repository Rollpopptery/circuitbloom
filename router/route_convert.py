"""Pure utility functions for path conversion and color generation."""

import hashlib


def net_color(name):
    """Deterministic color for a net name."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    hue = (h % 360) / 360.0
    # HSL to RGB (s=0.7, l=0.5)
    s, l = 0.7, 0.5
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((hue * 6) % 2 - 1))
    m = l - c / 2
    if hue < 1/6:   r, g, b = c, x, 0
    elif hue < 2/6: r, g, b = x, c, 0
    elif hue < 3/6: r, g, b = 0, c, x
    elif hue < 4/6: r, g, b = 0, x, c
    elif hue < 5/6: r, g, b = x, 0, c
    else:            r, g, b = c, 0, x
    R, G, B = int((r+m)*255), int((g+m)*255), int((b+m)*255)
    return f"#{R:02x}{G:02x}{B:02x}"


def path_to_segments(grid, path, net_name, layer_names, width=0.25,
                     start_mm=None, end_mm=None):
    """Convert A* cell path to track segment dicts.

    start_mm/end_mm: exact (x,y) pad positions to snap terminal endpoints to,
    avoiding grid rounding gaps.
    """
    if len(path) < 2:
        return []

    segments = []
    seg_start = path[0]
    prev_dir = None
    prev_layer = path[0][2]

    for i in range(1, len(path)):
        cx, cy, cl = path[i]
        px, py, pl = path[i-1]

        if cl != pl:
            # Layer change (via) — end current segment
            if seg_start != path[i-1]:
                sx, sy = grid.grid_to_mm(seg_start[0], seg_start[1])
                ex, ey = grid.grid_to_mm(px, py)
                segments.append({
                    "x1": sx, "y1": sy, "x2": ex, "y2": ey,
                    "width": width, "layer": layer_names[pl], "net": net_name
                })
            seg_start = path[i]
            prev_dir = None
            prev_layer = cl
            continue

        cur_dir = (cx - px, cy - py)
        if prev_dir is not None and cur_dir != prev_dir:
            # Direction change — end segment
            sx, sy = grid.grid_to_mm(seg_start[0], seg_start[1])
            ex, ey = grid.grid_to_mm(px, py)
            segments.append({
                "x1": sx, "y1": sy, "x2": ex, "y2": ey,
                "width": width, "layer": layer_names[cl], "net": net_name
            })
            seg_start = path[i-1]

        prev_dir = cur_dir

    # Final segment
    if seg_start != path[-1]:
        sx, sy = grid.grid_to_mm(seg_start[0], seg_start[1])
        ex, ey = grid.grid_to_mm(path[-1][0], path[-1][1])
        segments.append({
            "x1": sx, "y1": sy, "x2": ex, "y2": ey,
            "width": width, "layer": layer_names[path[-1][2]], "net": net_name
        })

    # Snap terminal endpoints to exact pad positions
    if segments and start_mm:
        segments[0]["x1"] = start_mm[0]
        segments[0]["y1"] = start_mm[1]
    if segments and end_mm:
        segments[-1]["x2"] = end_mm[0]
        segments[-1]["y2"] = end_mm[1]

    return segments


def path_vias(path):
    """Extract via positions from path (layer transitions)."""
    vias = []
    for i in range(1, len(path)):
        if path[i][2] != path[i-1][2]:
            vias.append((path[i][0], path[i][1]))
    return vias
