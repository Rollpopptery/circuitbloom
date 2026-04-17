#!/usr/bin/env python3
"""
kicad_to_svg.py — Capture live KiCad board and convert to SVG.

Pads as circles with ref:pin labels, tracks as lines grouped by layer,
vias as annular rings. All coordinates in mm, viewBox in mm directly.

Usage:
    from kicad_to_svg import board_to_svg, capture_and_convert

    svg_str = capture_and_convert()               # auto-detect socket
    svg_str = capture_and_convert(socket_path)    # explicit socket
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'router'))
from grab_layer import capture_board, find_socket


# ── Layer colours ────────────────────────────────────────────────────────────
LAYER_COLOURS = {
    "F.Cu":  "#c83232",
    "B.Cu":  "#3264c8",
    "In1.Cu":"#c8a000",
    "In2.Cu":"#00a0c8",
    "In3.Cu":"#a000c8",
    "In4.Cu":"#00c8a0",
}
DEFAULT_LAYER_COLOUR = "#888888"

PAD_COLOUR          = "#d4a000"
PAD_LABEL_COLOUR    = "#ffffff"
VIA_COLOUR          = "#aaaaaa"
VIA_DRILL_COLOUR    = "#1a1a1a"
BOARD_COLOUR        = "#1a1a1a"
OUTLINE_COLOUR      = "#444444"


def _layer_colour(layer: str) -> str:
    return LAYER_COLOURS.get(layer, DEFAULT_LAYER_COLOUR)


def _snap(v, origin):
    """Subtract origin and round to 4 decimal places."""
    return round(v - origin, 4)


def board_to_svg(data: dict) -> str:
    """
    Convert raw capture_board() data to an SVG string.

    Args:
        data: dict returned by capture_board()

    Returns:
        SVG as a string, coordinates in mm, viewBox in mm.
    """
    bounds = data["bounds"]
    origin_x = bounds["min_x"]
    origin_y = bounds["min_y"]
    board_w  = round(bounds["max_x"] - origin_x, 4)
    board_h  = round(bounds["max_y"] - origin_y, 4)

    pads        = data["pads"]
    tracks      = data["tracks"]
    vias        = data["vias"]
    copper_layers = data.get("copper_layers", ["F.Cu", "B.Cu"])

    # Pad radius — reasonable default in mm
    pad_r = 0.25

    lines = []

    # ── SVG header ────────────────────────────────────────────────────────────
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="0 0 {board_w} {board_h}"'
        f' width="{board_w}mm" height="{board_h}mm"'
        f' style="background:{BOARD_COLOUR}">'
    )

    # ── Style block — pad labels togglable via .pad-labels class ─────────────
    lines.append('<style>')
    lines.append('  .pad-label { font-family: monospace; font-size: 0.4px;'
                 f' fill: {PAD_LABEL_COLOUR}; text-anchor: middle;'
                 ' dominant-baseline: central; pointer-events: none; }')
    lines.append('  .labels-hidden .pad-label { display: none; }')
    lines.append('</style>')

    # ── Board outline ─────────────────────────────────────────────────────────
    lines.append(
        f'<rect x="0" y="0" width="{board_w}" height="{board_h}"'
        f' fill="none" stroke="{OUTLINE_COLOUR}" stroke-width="0.1"/>'
    )

    # ── Tracks — one <g> per layer ────────────────────────────────────────────
    # Group tracks by layer
    by_layer: dict[str, list] = {name: [] for name in copper_layers}
    for t in tracks:
        layer = t.get("layer", "")
        if layer not in by_layer:
            by_layer[layer] = []
        by_layer[layer].append(t)

    for layer_name, layer_tracks in by_layer.items():
        if not layer_tracks:
            continue
        colour = _layer_colour(layer_name)
        safe_id = layer_name.replace(".", "_").replace(" ", "_")
        lines.append(f'<g id="layer-{safe_id}" data-layer="{layer_name}">')
        for t in layer_tracks:
            x1 = _snap(t["x1"], origin_x)
            y1 = _snap(t["y1"], origin_y)
            x2 = _snap(t["x2"], origin_x)
            y2 = _snap(t["y2"], origin_y)
            w  = t.get("width", 0.1)
            net = t.get("net", "")
            lines.append(
                f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"'
                f' stroke="{colour}" stroke-width="{w}"'
                f' stroke-linecap="round"'
                f' data-net="{net}"/>'
            )
        lines.append('</g>')

    # ── Vias ──────────────────────────────────────────────────────────────────
    lines.append('<g id="vias">')
    for v in vias:
        cx  = _snap(v["x"], origin_x)
        cy  = _snap(v["y"], origin_y)
        r_o = v.get("od", 0.6) / 2
        r_i = v.get("id", 0.3) / 2
        net = v.get("net", "")
        lines.append(
            f'  <circle cx="{cx}" cy="{cy}" r="{r_o}"'
            f' fill="{VIA_COLOUR}" data-net="{net}"/>'
        )
        lines.append(
            f'  <circle cx="{cx}" cy="{cy}" r="{r_i}"'
            f' fill="{VIA_DRILL_COLOUR}"/>'
        )
    lines.append('</g>')

    # ── Pads with labels ──────────────────────────────────────────────────────
    lines.append('<g id="pads">')
    for p in pads:
        cx  = _snap(p["x"], origin_x)
        cy  = _snap(p["y"], origin_y)
        net = p.get("net", "")
        ref = p.get("ref", "")
        pin = p.get("pin", "")
        label = f"{ref}:{pin}" if ref else pin

        lines.append(
            f'  <circle cx="{cx}" cy="{cy}" r="{pad_r}"'
            f' fill="{PAD_COLOUR}" data-ref="{ref}" data-pin="{pin}" data-net="{net}"/>'
        )
        if label:
            lines.append(
                f'  <text class="pad-label" x="{cx}" y="{cy}">{label}</text>'
            )
    lines.append('</g>')

    # ── Footer ────────────────────────────────────────────────────────────────
    lines.append('</svg>')

    return "\n".join(lines)


def capture_and_convert(socket_path: str = None) -> tuple[bool, str]:
    """
    Find KiCad socket, capture board, return SVG string.

    Args:
        socket_path: explicit IPC socket path, or None to auto-detect

    Returns:
        (ok, result) — if ok is True, result is SVG string;
                       if ok is False, result is error message.
    """
    if socket_path is None:
        socket_path = find_socket()
    if not socket_path:
        return False, "no KiCad socket found in /tmp/kicad/"

    try:
        data = capture_board(socket_path, pitch_mm=0.1)
    except Exception as e:
        return False, f"capture failed: {e}"

    n_pads   = len(data["pads"])
    n_tracks = len(data["tracks"])
    n_vias   = len(data["vias"])
    print(f"  captured: {n_pads} pads, {n_tracks} tracks, {n_vias} vias")

    svg = board_to_svg(data)
    return True, svg