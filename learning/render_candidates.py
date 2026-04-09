#!/usr/bin/env python3
"""
render_candidates.py — Render candidate traces overlaid on the board layout.

Retrieves matching traces, transforms them, and draws them all on one image
with the board's pads visible. Each candidate is a different colour.

Usage:
    python3 render_candidates.py --from-pad XU1.18 --to-pad U4.12 --net SPI_SCK -o candidates.png
"""

import argparse
import json
import math
import os
import sys
import urllib.request

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from pad_pattern_render import count_component_pins, choose_source_dest
from trace_transform import retrieve_and_transform
from trace_patterndb import _load_dino

import chromadb

SERVER = "http://localhost:8084"

# Candidate trace colours — distinct, visible on dark background
TRACE_COLOURS = [
    (255, 100, 100),   # red
    (100, 255, 100),   # green
    (100, 100, 255),   # blue
    (255, 255, 100),   # yellow
    (255, 100, 255),   # magenta
    (100, 255, 255),   # cyan
    (255, 180, 100),   # orange
    (180, 100, 255),   # purple
    (100, 255, 180),   # teal
    (255, 100, 180),   # pink
]

PAD_COLOUR = (160, 120, 60)       # brownish — component pads
PAD_SAME_NET = (255, 255, 255)    # white — source/dest net pads
PAD_GND = (60, 160, 60)           # dark green — GND pads
BOARD_BG = (30, 30, 30)           # dark grey background


def find_pad(pads, ref_pin):
    """Find a pad by REF.PIN string."""
    parts = ref_pin.split(".", 1)
    if len(parts) != 2:
        return None
    ref, pin = parts
    for p in pads:
        if p["ref"] == ref and p["pin"] == pin:
            return p
    return None


def render_board_with_candidates(pads, candidates, net, board_dims,
                                 source, dest, width=800):
    """Render board pads and candidate traces to an image.

    Args:
        pads: list of pad dicts
        candidates: list of transformed candidate dicts
        net: the net being routed
        board_dims: (width_mm, height_mm)
        source: (x, y) source pad
        dest: (x, y) destination pad
        width: image width in pixels

    Returns:
        PIL Image
    """
    board_w, board_h = board_dims
    scale = width / board_w
    height = int(board_h * scale)

    img = Image.new("RGB", (width, height), BOARD_BG)
    draw = ImageDraw.Draw(img)

    def mm_to_px(x, y):
        return int(x * scale), int(y * scale)

    # Draw all pads
    pad_r = max(2, int(0.4 * scale))
    for p in pads:
        px, py = mm_to_px(p["x"], p["y"])
        pad_net = p.get("net", "")

        if pad_net == net:
            colour = PAD_SAME_NET
        elif pad_net.upper() in ("GND", "/GND", "GNDD", "GNDA", "AGND", "DGND"):
            colour = PAD_GND
        elif pad_net:
            colour = PAD_COLOUR
        else:
            colour = (80, 80, 80)

        draw.ellipse([px - pad_r, py - pad_r, px + pad_r, py + pad_r],
                     fill=colour)

    # Draw candidate traces
    for i, candidate in enumerate(candidates):
        colour = TRACE_COLOURS[i % len(TRACE_COLOURS)]
        segments = candidate["segments"]
        trace_width = max(1, int(0.25 * scale))

        for seg in segments:
            x1, y1 = mm_to_px(seg[0], seg[1])
            x2, y2 = mm_to_px(seg[2], seg[3])
            draw.line([x1, y1, x2, y2], fill=colour, width=trace_width)

        # Draw vias
        via_r = max(2, int(0.3 * scale))
        for via in candidate["vias"]:
            vx, vy = mm_to_px(via[0], via[1])
            draw.ellipse([vx - via_r, vy - via_r, vx + via_r, vy + via_r],
                         outline=colour, width=1)

    # Mark source and destination
    marker_r = max(4, int(0.6 * scale))
    sx, sy = mm_to_px(source[0], source[1])
    dx, dy = mm_to_px(dest[0], dest[1])
    draw.ellipse([sx - marker_r, sy - marker_r, sx + marker_r, sy + marker_r],
                 outline=(255, 255, 255), width=2)
    draw.ellipse([dx - marker_r, dy - marker_r, dx + marker_r, dy + marker_r],
                 outline=(255, 255, 255), width=2)

    return img


def main():
    parser = argparse.ArgumentParser(description="Render candidate traces")
    parser.add_argument("--from-pad", required=True, help="Source pad (e.g. XU1.18)")
    parser.add_argument("--to-pad", required=True, help="Destination pad (e.g. U4.12)")
    parser.add_argument("--net", required=True, help="Net name")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "trace_pattern_collection"))
    parser.add_argument("--n", type=int, default=5, help="Number of candidates")
    parser.add_argument("-o", "--output", default="candidates.png", help="Output image path")
    parser.add_argument("--width", type=int, default=800, help="Image width in pixels")
    args = parser.parse_args()

    # Load DINOv2
    print("Loading DINOv2...")
    _load_dino()

    # Open collection
    client = chromadb.PersistentClient(path=os.path.abspath(args.db))
    collection = client.get_collection("trace_patterns")
    print(f"Collection: {collection.count()} patterns")

    # Fetch board state
    print("Fetching board state...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    pads = data.get("pads", [])
    board = data.get("board", {})
    board_w = board.get("width", 60)
    board_h = board.get("height", 40)
    pad_counts = count_component_pins(pads)

    # Find pads
    src_pad = find_pad(pads, args.from_pad)
    dst_pad = find_pad(pads, args.to_pad)
    if not src_pad:
        print(f"Pad {args.from_pad} not found")
        return
    if not dst_pad:
        print(f"Pad {args.to_pad} not found")
        return

    # Consistent ordering
    src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)
    source = (src_pad["x"], src_pad["y"])
    dest = (dst_pad["x"], dst_pad["y"])

    print(f"Query: {args.net}")
    print(f"  {src_pad['ref']}.{src_pad['pin']} -> {dst_pad['ref']}.{dst_pad['pin']}")
    print(f"  Length: {math.hypot(dest[0]-source[0], dest[1]-source[1]):.2f}mm")

    # Retrieve and transform
    candidates = retrieve_and_transform(
        collection, pads, args.net, source, dest, n=args.n
    )

    print(f"  {len(candidates)} candidates")

    # Render
    img = render_board_with_candidates(
        pads, candidates, args.net, (board_w, board_h),
        source, dest, width=args.width
    )

    img.save(args.output)
    print(f"\nSaved to {args.output}")

    # Legend
    for i, c in enumerate(candidates):
        meta = c["source_meta"]
        colour_name = ["red", "green", "blue", "yellow", "magenta",
                       "cyan", "orange", "purple", "teal", "pink"][i % 10]
        print(f"  {colour_name}: [{meta.get('board', '?')}] "
              f"{meta.get('net', '?')} scale={c['transform']['scale']:.3f}")


if __name__ == "__main__":
    main()