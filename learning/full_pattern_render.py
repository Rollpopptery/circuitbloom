#!/usr/bin/env python3
"""
full_pattern_render.py — Render pad and trace patterns as 224x224 images.

Extends pad_pattern_render by also drawing existing traces as 1-pixel lines.
Colour scheme is consistent — colour encodes net relationship:

  - Red dot / Red line:   foreign net pad / trace
  - Blue dot / Blue line: same net pad / trace
  - Green dot / Green line: GND pad / trace
  - Black: empty space

Traces are drawn first, pads on top, so pad dots are always visible.

The coordinate frame is rotation-normalised before rendering so that
src->dst always points along +X, identical to pad_pattern_render.py.

Usage:
    from full_pattern_render import render_full_pattern, render_full_and_save

    img = render_full_pattern(pads, tracks, route_net, source_pt, dest_pt)
    img.save("pattern.png")

To use in trace_patterndb.py instead of pad_pattern_render:
    from full_pattern_render import render_full_pattern as render_pad_pattern
    from full_pattern_render import get_source_dest_in_grid, count_component_pins
    from full_pattern_render import choose_source_dest, count_visible_obstacles
"""

import math

from PIL import Image, ImageDraw

from pad_pattern_render import (
    IMAGE_SIZE,
    PAD_RADIUS,
    MARGIN_RATIO,
    MIN_WINDOW_MM,
    COL_EMPTY,
    COL_FOREIGN,
    COL_SAME_NET,
    COL_GND,
    GND_NAMES,
    _is_gnd_net,
    _canonical_frame,
    _bounding_box,
    _to_pixel,
    count_component_pins,
    choose_source_dest,
    get_source_dest_in_grid,
)


# ============================================================
# OBSTACLE COUNT — same as pad_pattern_render but unchanged
# ============================================================

def count_visible_obstacles(pads, route_net, source_pt, dest_pt,
                            margin_ratio=MARGIN_RATIO):
    """Count non-source-net pads visible in the rendering window.

    Same as pad_pattern_render.count_visible_obstacles — counts pads only,
    not traces, since this is used as a collection quality filter.
    """
    rot_pads, rot_source, rot_dest = _canonical_frame(pads, source_pt, dest_pt)

    min_x, min_y, max_x, max_y, width, height = _bounding_box(
        rot_source, rot_dest, margin_ratio
    )

    count = 0
    for pad in rot_pads:
        px = pad.get("x", 0)
        py = pad.get("y", 0)
        pad_net = pad.get("net", "")

        if px < min_x or px > max_x or py < min_y or py > max_y:
            continue

        if pad_net == route_net or not pad_net:
            continue

        count += 1

    return count


# ============================================================
# TRACE DRAWING
# ============================================================

def _draw_traces(draw, tracks, route_net, min_x, min_y, width, height):
    """Draw all tracks within the window as 1-pixel lines.

    Args:
        draw: ImageDraw instance
        tracks: list of track dicts with {x1, y1, x2, y2, net}
        route_net: the net being routed
        min_x, min_y: bounding box origin (in rotated frame)
        width, height: bounding box size
    """
    for t in tracks:
        net = t.get("net", "")
        if not net:
            continue

        if net == route_net:
            continue

        # Determine colour
        if net == route_net:
            colour = COL_SAME_NET
        elif _is_gnd_net(net):
            colour = COL_GND
        else:
            colour = COL_FOREIGN

        px1, py1 = _to_pixel(t["x1"], t["y1"], min_x, min_y, width, height)
        px2, py2 = _to_pixel(t["x2"], t["y2"], min_x, min_y, width, height)

        # Skip if both endpoints are outside image
        if (px1 < 0 and px2 < 0) or (px1 >= IMAGE_SIZE and px2 >= IMAGE_SIZE):
            continue
        if (py1 < 0 and py2 < 0) or (py1 >= IMAGE_SIZE and py2 >= IMAGE_SIZE):
            continue

        draw.line([(px1, py1), (px2, py2)], fill=colour, width=1)


# ============================================================
# RENDER
# ============================================================

def render_full_pattern(pads, tracks, route_net, source_pt, dest_pt,
                        margin_ratio=MARGIN_RATIO):
    """Render pads and traces as coloured dots and lines on a 224x224 image.

    The coordinate frame is rotation-normalised so src->dst always points
    along +X, identical to pad_pattern_render.render_pad_pattern.

    Args:
        pads: list of pad dicts with {x, y, net, ref, pin}
        tracks: list of track dicts with {x1, y1, x2, y2, net, layer}
        route_net: the net being routed
        source_pt: (x, y) source point in mm
        dest_pt: (x, y) destination point in mm
        margin_ratio: fraction of trace length to add as margin

    Returns:
        PIL Image (224x224 RGB)
    """
    # Rotate everything into canonical frame
    rot_pads, rot_source, rot_dest = _canonical_frame(pads, source_pt, dest_pt)

    # Rotate tracks too
    sx, sy = source_pt
    dx, dy = dest_pt
    theta = math.atan2(dy - sy, dx - sx)
    cos_t = math.cos(-theta)
    sin_t = math.sin(-theta)

    def _rotate(x, y):
        rx = (x - sx) * cos_t - (y - sy) * sin_t + sx
        ry = (x - sx) * sin_t + (y - sy) * cos_t + sy
        return rx, ry

    rot_tracks = []
    for t in tracks:
        rx1, ry1 = _rotate(t["x1"], t["y1"])
        rx2, ry2 = _rotate(t["x2"], t["y2"])
        rot_tracks.append({**t, "x1": rx1, "y1": ry1, "x2": rx2, "y2": ry2})

    min_x, min_y, max_x, max_y, width, height = _bounding_box(
        rot_source, rot_dest, margin_ratio
    )

    img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), COL_EMPTY)
    draw = ImageDraw.Draw(img)

    # Draw traces first (1px lines) — pads drawn on top
    _draw_traces(draw, rot_tracks, route_net, min_x, min_y, width, height)

    # Draw pads on top
    for pad in rot_pads:
        px, py = _to_pixel(pad["x"], pad["y"], min_x, min_y, width, height)

        if px < 0 or px >= IMAGE_SIZE or py < 0 or py >= IMAGE_SIZE:
            continue

        pad_net = pad.get("net", "")

        if pad_net == route_net:
            colour = COL_SAME_NET
        elif _is_gnd_net(pad_net):
            colour = COL_GND
        elif pad_net:
            colour = COL_FOREIGN
        else:
            continue  # skip unconnected pads

        draw.ellipse(
            [px - PAD_RADIUS, py - PAD_RADIUS,
             px + PAD_RADIUS, py + PAD_RADIUS],
            fill=colour
        )

    return img


def render_full_and_save(pads, tracks, route_net, source_pt, dest_pt, path,
                         margin_ratio=MARGIN_RATIO):
    """Render a full pattern and save to file.

    Args:
        pads: list of pad dicts
        tracks: list of track dicts
        route_net: net being routed
        source_pt: (x, y) source
        dest_pt: (x, y) destination
        path: output file path
        margin_ratio: margin ratio
    """
    img = render_full_pattern(pads, tracks, route_net, source_pt, dest_pt,
                              margin_ratio)
    img.save(path)


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    """Quick test — render full patterns from route server."""
    import json
    import os
    import sys
    import urllib.request

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))
    from rebuild_routes import rebuild_routes

    SERVER = "http://localhost:8084"
    OUT_DIR = "test_full_patterns"

    print("Fetching board state...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    tracks = data.get("tracks", [])
    pads = data.get("pads", [])
    vias = data.get("vias", [])

    routes = rebuild_routes(tracks, pads, vias)
    print(f"  {len(routes)} routes")

    os.makedirs(OUT_DIR, exist_ok=True)

    rendered = 0
    for route in routes:
        if not route.get("from_pad") or not route.get("to_pad"):
            continue

        source = route["from_pt"]
        dest = route["to_pt"]
        trace_len = math.hypot(dest[0] - source[0], dest[1] - source[1])
        if trace_len < 0.5:
            continue

        net = route["net"]
        from_ref = route["from_pad"]["ref"]
        from_pin = route["from_pad"]["pin"]
        to_ref = route["to_pad"]["ref"]
        to_pin = route["to_pad"]["pin"]

        filename = f"{net}__{from_ref}.{from_pin}__{to_ref}.{to_pin}.png"
        filename = filename.replace("/", "_").replace("\\", "_")
        filepath = os.path.join(OUT_DIR, filename)

        render_full_and_save(pads, tracks, net, source, dest, filepath)
        rendered += 1

        if rendered >= 20:
            break

    print(f"\nRendered {rendered} patterns to {OUT_DIR}/")