#!/usr/bin/env python3
"""
board_render.py — Render board state to PNG image.

Renders pads, tracks, vias, and component outlines to a PNG image
for a specified rectangle of the board. Returns base64-encoded PNG.

Usage:
    from board_render import render_board

    png_bytes = render_board(state, x1=10, y1=15, x2=30, y2=25, width=600)
    # Returns PNG as bytes

    b64 = render_board_b64(state, x1=10, y1=15, x2=30, y2=25, width=600)
    # Returns base64 data URL string
"""

import base64
import io
import math

from PIL import Image, ImageDraw, ImageFont


# Colours matching KiCad convention
COL_BACKGROUND = (26, 26, 26)
COL_FCU = (204, 68, 34)       # red
COL_BCU = (34, 102, 204)      # blue
COL_PAD_FCU = (180, 120, 60)  # copper/brown
COL_PAD_BCU = (60, 100, 160)
COL_VIA = (200, 200, 60)      # yellow
COL_RATSNEST = (80, 80, 80)   # dark grey
COL_OUTLINE = (200, 200, 0)   # board outline
COL_TEXT = (180, 180, 180)
COL_REF = (140, 140, 140)

LAYER_COLORS = {
    "F.Cu": COL_FCU,
    "B.Cu": COL_BCU,
}


def _get_layer_color(layer_name):
    return LAYER_COLORS.get(layer_name, COL_FCU)


def render_board(state, x1=None, y1=None, x2=None, y2=None,
                 width=800, show_ratsnest=True, show_refs=True,
                 pad_radius=0.4):
    """Render board state to PNG bytes.

    Args:
        state: route server state dict with pads, tracks, vias, board
        x1, y1, x2, y2: viewport in board mm coords (None = full board)
        width: output image width in pixels
        show_ratsnest: draw unrouted connections as grey lines
        show_refs: draw component reference labels
        pad_radius: pad size in mm for rendering

    Returns:
        PNG image as bytes
    """
    board = state.get("board", {})
    pads = state.get("pads", [])
    tracks = state.get("tracks", [])
    vias = state.get("vias", [])

    # Determine viewport
    if x1 is None:
        x1 = 0
    if y1 is None:
        y1 = 0
    if x2 is None:
        x2 = board.get("width", 55)
    if y2 is None:
        y2 = board.get("height", 40)

    board_w = x2 - x1
    board_h = y2 - y1
    if board_w <= 0 or board_h <= 0:
        return None

    # Calculate image dimensions
    scale = width / board_w
    height = int(board_h * scale)

    img = Image.new("RGB", (width, height), COL_BACKGROUND)
    draw = ImageDraw.Draw(img)

    def to_px(bx, by):
        px = int((bx - x1) * scale)
        py = int((by - y1) * scale)
        return px, py

    def mm_to_px(mm):
        return max(1, int(mm * scale))

    # Draw board outline
    ox1, oy1 = to_px(0, 0)
    ox2, oy2 = to_px(board.get("width", 55), board.get("height", 40))
    draw.rectangle([ox1, oy1, ox2, oy2], outline=COL_OUTLINE, width=1)

    # Draw tracks — B.Cu first (underneath), F.Cu on top
    for layer_order in ["B.Cu", "F.Cu"]:
        color = _get_layer_color(layer_order)
        for t in tracks:
            if t["layer"] != layer_order:
                continue
            px1, py1 = to_px(t["x1"], t["y1"])
            px2, py2 = to_px(t["x2"], t["y2"])
            tw = mm_to_px(t.get("width", 0.25))
            draw.line([px1, py1, px2, py2], fill=color, width=tw)

    # Draw pads
    for p in pads:
        px, py = to_px(p["x"], p["y"])
        pr = mm_to_px(pad_radius)
        if p.get("smd"):
            col = COL_PAD_FCU
        else:
            col = COL_PAD_FCU  # through-hole, show on top
        draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=col)
        # Drill hole for through-hole
        if not p.get("smd"):
            hr = max(1, pr // 3)
            draw.ellipse([px - hr, py - hr, px + hr, py + hr], fill=COL_BACKGROUND)

    # Draw vias
    for v in vias:
        px, py = to_px(v["x"], v["y"])
        vr = mm_to_px(0.3)
        draw.ellipse([px - vr, py - vr, px + vr, py + vr], fill=COL_VIA)
        hr = max(1, vr // 3)
        draw.ellipse([px - hr, py - hr, px + hr, py + hr], fill=COL_BACKGROUND)

    # Draw ratsnest (unrouted connections)
    if show_ratsnest:
        # Build set of routed nets
        routed_pad_pairs = set()
        net_pads = {}
        for p in pads:
            if p["net"]:
                net_pads.setdefault(p["net"], []).append(p)

        routed_nets = set(t["net"] for t in tracks)

        for net, np_list in net_pads.items():
            if net in routed_nets or len(np_list) < 2:
                continue
            # Draw ratsnest as nearest-neighbour chain
            remaining = list(np_list)
            current = remaining.pop(0)
            while remaining:
                best = min(remaining, key=lambda p: (p["x"] - current["x"])**2 + (p["y"] - current["y"])**2)
                px1, py1 = to_px(current["x"], current["y"])
                px2, py2 = to_px(best["x"], best["y"])
                draw.line([px1, py1, px2, py2], fill=COL_RATSNEST, width=1)
                remaining.remove(best)
                current = best

    # Draw reference labels
    if show_refs:
        seen_refs = set()
        for p in pads:
            ref = p.get("ref", "")
            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                px, py = to_px(p["x"], p["y"])
                # Offset label slightly
                try:
                    draw.text((px + 3, py - 10), ref, fill=COL_REF)
                except Exception:
                    pass

    # Output
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_board_b64(state, **kwargs):
    """Render board to base64 data URL string."""
    png = render_board(state, **kwargs)
    if png is None:
        return None
    b64 = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def save_render(state, path, **kwargs):
    """Render board and save to file."""
    png = render_board(state, **kwargs)
    if png:
        with open(path, "wb") as f:
            f.write(png)
        return True
    return False


if __name__ == "__main__":
    import json
    import urllib.request

    state = json.loads(urllib.request.urlopen("http://localhost:8084/").read())

    # Full board
    save_render(state, "/tmp/board_full.png", width=1200)
    print("Saved /tmp/board_full.png")

    # Zoomed to XU1 area
    save_render(state, "/tmp/board_xu1.png", x1=10, y1=15, x2=30, y2=35, width=800)
    print("Saved /tmp/board_xu1.png")
