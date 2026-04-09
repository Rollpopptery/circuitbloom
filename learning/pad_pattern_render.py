#!/usr/bin/env python3
"""
pad_pattern_render.py — Render pad patterns as 224x224 colour-coded images.

Renders pad centres as coloured dots on a black background:
  - Red:   foreign net pad (obstacle)
  - Blue:  same net pad (route's net)
  - Green: GND pad
  - Black: empty space

The coordinate frame is rotation-normalised before rendering:
  - src->dst is always mapped to the +X axis
  - All pad positions are rotated accordingly
  - The stored segments payload is unaffected (original board coords)

Usage:
    from pad_pattern_render import render_pad_pattern, render_and_save

    img = render_pad_pattern(pads, route_net, source_pt, dest_pt)
    img.save("pattern.png")
"""

import math

import numpy as np
from PIL import Image, ImageDraw

# ============================================================
# CONSTANTS
# ============================================================

IMAGE_SIZE = 224
PAD_RADIUS = 3  # pixels
MARGIN_RATIO = 0.25  # 25% margin around trace bounding box
MIN_WINDOW_MM = 1.0  # minimum window size to avoid degenerate cases

# Colours (RGB)
COL_EMPTY = (0, 0, 0)        # black background
COL_FOREIGN = (255, 0, 0)     # red — foreign net
COL_SAME_NET = (0, 0, 255)    # blue — same net
COL_GND = (0, 255, 0)         # green — GND pad

# GND net name variants
GND_NAMES = {"GND", "/GND", "GNDD", "GNDA", "AGND", "DGND"}


def _is_gnd_net(net_name):
    """Check if a net name is a ground net."""
    return net_name.upper() in GND_NAMES


# ============================================================
# SOURCE / DESTINATION SELECTION
# ============================================================

def count_component_pins(pads):
    """Count distinct routed pins per component reference.

    Args:
        pads: list of pad dicts with {ref, pin, ...}

    Returns:
        dict of ref -> pin count
    """
    from collections import defaultdict
    counts = defaultdict(set)
    for p in pads:
        ref = p.get("ref", "")
        pin = p.get("pin", "")
        if ref and pin:
            counts[ref].add(pin)
    return {ref: len(pins) for ref, pins in counts.items()}


def choose_source_dest(from_pad, to_pad, pad_counts):
    """Consistently pick source and destination for a route.

    Higher pin count component is source.
    Tiebreak: lower x, then lower y.

    Args:
        from_pad: dict with {ref, pin, x, y, ...} or None
        to_pad: dict with {ref, pin, x, y, ...} or None
        pad_counts: dict of ref -> pin count (from count_component_pins)

    Returns:
        (source_pad, dest_pad) — consistent ordering
    """
    if not from_pad or not to_pad:
        return from_pad, to_pad

    from_ref = from_pad.get("ref", "")
    to_ref = to_pad.get("ref", "")

    from_pins = pad_counts.get(from_ref, 0)
    to_pins = pad_counts.get(to_ref, 0)

    if from_pins > to_pins:
        return from_pad, to_pad
    elif to_pins > from_pins:
        return to_pad, from_pad

    from_x = from_pad.get("x", 0)
    to_x = to_pad.get("x", 0)
    if from_x < to_x:
        return from_pad, to_pad
    elif to_x < from_x:
        return to_pad, from_pad

    from_y = from_pad.get("y", 0)
    to_y = to_pad.get("y", 0)
    if from_y < to_y:
        return from_pad, to_pad
    else:
        return to_pad, from_pad


# ============================================================
# ROTATION NORMALISATION
# ============================================================

def _canonical_frame(pads, source_pt, dest_pt):
    """Rotate pad coordinates into a canonical frame where src->dst = +X.

    The rotation is applied around the source point so that:
      - source maps to itself
      - dest maps to (source_x + trace_len, source_y)
      - all pads are rotated by the same angle

    This makes the rendered image invariant to trace orientation on the
    board. The original pad dicts are not mutated — new dicts are returned.

    Args:
        pads: list of pad dicts with {x, y, ...}
        source_pt: (x, y) source in mm
        dest_pt: (x, y) destination in mm

    Returns:
        (rotated_pads, rotated_source, rotated_dest)
    """
    sx, sy = source_pt
    dx, dy = dest_pt

    theta = math.atan2(dy - sy, dx - sx)
    cos_t = math.cos(-theta)
    sin_t = math.sin(-theta)

    def _rotate(x, y):
        rx = (x - sx) * cos_t - (y - sy) * sin_t + sx
        ry = (x - sx) * sin_t + (y - sy) * cos_t + sy
        return rx, ry

    rotated_pads = []
    for pad in pads:
        rx, ry = _rotate(pad["x"], pad["y"])
        rotated_pads.append({**pad, "x": rx, "y": ry})

    rot_source = source_pt
    rot_dest = _rotate(dx, dy)

    return rotated_pads, rot_source, rot_dest


# ============================================================
# BOUNDING BOX
# ============================================================

def _bounding_box(source_pt, dest_pt, margin_ratio=MARGIN_RATIO):
    """Calculate a square bounding box for a trace with margin.

    Always returns a square window to preserve true spatial relationships.
    Equal scaling in X and Y ensures pad patterns are geometrically accurate.

    Args:
        source_pt: (x, y) source point in mm
        dest_pt: (x, y) destination point in mm
        margin_ratio: fraction of trace length to add as margin

    Returns:
        (min_x, min_y, max_x, max_y, width, height)
    """
    sx, sy = source_pt
    dx, dy = dest_pt

    min_x = min(sx, dx)
    max_x = max(sx, dx)
    min_y = min(sy, dy)
    max_y = max(sy, dy)

    trace_len = math.hypot(dx - sx, dy - sy)
    if trace_len < MIN_WINDOW_MM:
        trace_len = MIN_WINDOW_MM

    margin = trace_len * margin_ratio

    min_x -= margin
    max_x += margin
    min_y -= margin
    max_y += margin

    width = max_x - min_x
    height = max_y - min_y

    # Force square — use largest dimension for both sides
    max_dim = max(width, height)
    if max_dim < MIN_WINDOW_MM:
        max_dim = MIN_WINDOW_MM

    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    min_x = cx - max_dim / 2
    max_x = cx + max_dim / 2
    min_y = cy - max_dim / 2
    max_y = cy + max_dim / 2
    width = max_dim
    height = max_dim

    return min_x, min_y, max_x, max_y, width, height


# ============================================================
# OBSTACLE COUNT
# ============================================================

def count_visible_obstacles(pads, route_net, source_pt, dest_pt,
                            margin_ratio=MARGIN_RATIO):
    """Count non-source-net pads visible in the rendering window.

    Operates in the rotation-normalised frame so the window is consistent
    with what render_pad_pattern will actually render.

    Args:
        pads: list of pad dicts with {x, y, net, ...}
        route_net: the net being routed
        source_pt: (x, y) source point in mm
        dest_pt: (x, y) destination point in mm
        margin_ratio: margin ratio for bounding box

    Returns:
        int — number of obstacle pads in the window
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
# PIXEL MAPPING
# ============================================================

def _to_pixel(bx, by, min_x, min_y, width, height):
    """Map board coordinates to pixel coordinates.

    Args:
        bx, by: board coordinates in mm
        min_x, min_y: bounding box origin
        width, height: bounding box size

    Returns:
        (px, py) pixel coordinates
    """
    px = (bx - min_x) / width * (IMAGE_SIZE - 1)
    py = (by - min_y) / height * (IMAGE_SIZE - 1)
    return int(round(px)), int(round(py))


# ============================================================
# RENDER
# ============================================================

def render_pad_pattern(pads, route_net, source_pt, dest_pt,
                       margin_ratio=MARGIN_RATIO):
    """Render pad centres as coloured dots on a 224x224 black image.

    The coordinate frame is rotation-normalised before rendering so that
    src->dst always points along +X. This makes the embedding invariant
    to trace orientation on the board, and is applied identically at
    both collection time and query time.

    Args:
        pads: list of pad dicts with {x, y, net, ref, pin}
        route_net: the net being routed (pads on this net are blue)
        source_pt: (x, y) source point in mm
        dest_pt: (x, y) destination point in mm
        margin_ratio: fraction of trace length to add as margin

    Returns:
        PIL Image (224x224 RGB)
    """
    rot_pads, rot_source, rot_dest = _canonical_frame(pads, source_pt, dest_pt)

    min_x, min_y, max_x, max_y, width, height = _bounding_box(
        rot_source, rot_dest, margin_ratio
    )

    img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), COL_EMPTY)
    draw = ImageDraw.Draw(img)

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


def get_source_dest_in_grid(source_pt, dest_pt, margin_ratio=MARGIN_RATIO):
    """Get source and destination positions in grid coordinates (0-223).

    Uses the rotation-normalised frame, matching render_pad_pattern.

    Args:
        source_pt: (x, y) source point in mm
        dest_pt: (x, y) destination point in mm
        margin_ratio: margin ratio

    Returns:
        (src_gx, src_gy, dst_gx, dst_gy)
    """
    _, rot_source, rot_dest = _canonical_frame([], source_pt, dest_pt)

    min_x, min_y, max_x, max_y, width, height = _bounding_box(
        rot_source, rot_dest, margin_ratio
    )

    sgx, sgy = _to_pixel(rot_source[0], rot_source[1],
                          min_x, min_y, width, height)
    dgx, dgy = _to_pixel(rot_dest[0], rot_dest[1],
                          min_x, min_y, width, height)
    return sgx, sgy, dgx, dgy


def render_and_save(pads, route_net, source_pt, dest_pt, path,
                    margin_ratio=MARGIN_RATIO):
    """Render a pad pattern and save to file.

    Args:
        pads: list of pad dicts
        route_net: net being routed
        source_pt: (x, y) source
        dest_pt: (x, y) destination
        path: output file path (e.g. "pattern.png")
        margin_ratio: margin ratio
    """
    img = render_pad_pattern(pads, route_net, source_pt, dest_pt,
                             margin_ratio)
    img.save(path)


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    """Quick test — render patterns from route server."""
    import json
    import os
    import sys
    import urllib.request

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))
    from rebuild_routes import rebuild_routes

    SERVER = "http://localhost:8084"
    OUT_DIR = "test_patterns"

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

        render_and_save(pads, net, source, dest, filepath)
        rendered += 1

        if rendered >= 20:
            break

    print(f"\nRendered {rendered} patterns to {OUT_DIR}/")