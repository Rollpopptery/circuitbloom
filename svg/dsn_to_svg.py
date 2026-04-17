#!/usr/bin/env python3
"""
dsn_to_svg.py — Parse a Specctra SES session file and import routed
traces and vias into a BoardSVG instance as overlay elements.

No HTTP dependency. Takes a SES string and a BoardSVG instance.

Coordinate system:
    FreeRouting/Specctra SES uses math coordinates (Y up, origin bottom-left).
    SVG uses screen coordinates (Y down, origin top-left).
    All Y values are flipped on import:  y_svg = board_h - y_ses
    (The matching export flip is in svg_to_dsn.py:  y_dsn = board_h - y_svg)

SES via syntax (confirmed from real FreeRouting output):
    (via "Via[0-1]_600:300_um" x y)
    sits inside a (net "name" ...) block.

Usage:
    from dsn_to_svg import ses_to_svg
    count = ses_to_svg(ses_string, board)
"""

from __future__ import annotations
from board_svg import BoardSVG

# SES coordinates are in units of 0.1 microns (resolution um 10 means
# 10 units per micron). So: value / 10 = microns, / 1000 = mm → multiply by 0.0001
UM_TO_MM = 0.0001

LAYER_COLOURS = {
    "F.Cu": "#c83232",
    "B.Cu": "#3264c8",
}
DEFAULT_COLOUR = "#888888"

# Via display colours — match KiCad via style in board_svg.py
VIA_FILL       = "#aaaaaa"
VIA_DRILL_FILL = "#1a1a1a"


# ── S-expression tokeniser ────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif c == '(':
            tokens.append('(')
            i += 1
        elif c == ')':
            tokens.append(')')
            i += 1
        elif c == '"':
            i += 1
            start = i
            while i < n and text[i] != '"':
                i += 1
            tokens.append(text[start:i])
            i += 1
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n()\"':
                i += 1
            tokens.append(text[start:i])
    return tokens


def _parse(tokens: list[str], pos: int = 0) -> tuple:
    result = []
    while pos < len(tokens):
        t = tokens[pos]
        if t == '(':
            pos += 1
            node, pos = _parse(tokens, pos)
            result.append(node)
        elif t == ')':
            return result, pos + 1
        else:
            result.append(t)
            pos += 1
    return result, pos


def _find_all(node, key: str) -> list:
    results = []
    if isinstance(node, list):
        if node and node[0] == key:
            results.append(node)
        for child in node:
            results.extend(_find_all(child, key))
    return results


def _find_one(node, key: str):
    results = _find_all(node, key)
    return results[0] if results else None


# ── Via name parser ───────────────────────────────────────────────────────────

def _parse_via_name(name: str) -> tuple[float, float]:
    """
    Extract (od_mm, drill_mm) from a via padstack name.

    FreeRouting via names follow the pattern:
        Via[0-1]_<od>:<drill>_um
    Falls back to (0.6, 0.3) if parsing fails.
    """
    try:
        inner    = name.split('_', 1)[1]       # "<od>:<drill>_um"
        inner    = inner.rsplit('_', 1)[0]     # "<od>:<drill>"
        od_str, drill_str = inner.split(':')
        od_mm    = round(float(od_str)    * UM_TO_MM, 4)
        drill_mm = round(float(drill_str) * UM_TO_MM, 4)
        return od_mm, drill_mm
    except Exception:
        return 0.6, 0.3


# ── SES parser ────────────────────────────────────────────────────────────────

def parse_ses(ses_text: str) -> tuple[list[dict], list[dict]]:
    """
    Parse a Specctra SES file and return (wires, vias).

    Wire dicts:
        net, layer, width, points [(x_mm, y_mm) in SES math coords Y up]

    Via dicts:
        net, x, y (SES math coords Y up), od_mm, drill_mm
    """
    tokens = _tokenise(ses_text)
    tree, _ = _parse(tokens)
    root = tree[0] if tree else []

    wires = []
    vias  = []

    for net_node in _find_all(root, 'net'):
        if len(net_node) < 2:
            continue
        net_name = net_node[1]

        # ── Wires ─────────────────────────────────────────────────────────
        for wire_node in _find_all(net_node, 'wire'):
            path_node = _find_one(wire_node, 'path')
            if path_node is None or len(path_node) < 4:
                continue

            layer    = str(path_node[1]).replace('_', '.')
            width_mm = round(float(path_node[2]) * UM_TO_MM, 4)

            coords = path_node[3:]
            points = []
            for i in range(0, len(coords) - 1, 2):
                try:
                    x_mm = round(float(coords[i])     * UM_TO_MM, 4)
                    y_mm = round(float(coords[i + 1]) * UM_TO_MM, 4)
                    points.append((x_mm, y_mm))
                except (ValueError, IndexError):
                    pass

            if len(points) >= 2:
                wires.append({
                    'net':    net_name,
                    'layer':  layer,
                    'width':  width_mm,
                    'points': points,
                })

        # ── Vias — confirmed structure: (via "padstack" x y) ──────────────
        for via_node in _find_all(net_node, 'via'):
            if len(via_node) < 4:
                continue
            try:
                padstack_name = str(via_node[1])
                x_mm = round(float(via_node[2]) * UM_TO_MM, 4)
                y_mm = round(float(via_node[3]) * UM_TO_MM, 4)
            except (ValueError, IndexError):
                continue

            od_mm, drill_mm = _parse_via_name(padstack_name)
            vias.append({
                'net':      net_name,
                'x':        x_mm,
                'y':        y_mm,
                'od_mm':    od_mm,
                'drill_mm': drill_mm,
            })

    return wires, vias


# ── SVG import ────────────────────────────────────────────────────────────────

def ses_to_svg(ses_text: str, board: BoardSVG,
               prefix: str = 'route') -> int:
    """
    Parse SES and add routed traces and vias into board overlays.

    SES math coords (Y up) → SVG screen coords (Y down):
        y_svg = board_h - y_ses

    Via circles store data-od and data-drill so push_to_kicad()
    can recover the exact via dimensions without re-parsing.

    Returns total number of elements added (wires + vias).
    """
    wires, vias = parse_ses(ses_text)
    board_h     = board.board_h
    count       = 0

    # ── Traces ────────────────────────────────────────────────────────────────
    for i, wire in enumerate(wires):
        colour = LAYER_COLOURS.get(wire['layer'], DEFAULT_COLOUR)

        points_str = ' '.join(
            f"{x},{round(board_h - y, 4)}"
            for x, y in wire['points']
        )

        board.add_element('polyline', {
            'id':              f"{prefix}-{wire['net']}-{i}",
            'points':          points_str,
            'stroke':          colour,
            'stroke-width':    str(wire['width']),
            'stroke-linecap':  'round',
            'stroke-linejoin': 'round',
            'fill':            'none',
            'data-net':        wire['net'],
            'data-layer':      wire['layer'],
        })
        count += 1

    # ── Vias ──────────────────────────────────────────────────────────────────
    for i, via in enumerate(vias):
        cx = via['x']
        cy = round(board_h - via['y'], 4)   # flip Y

        od    = via['od_mm']
        drill = via['drill_mm']
        r_outer = round(od    / 2, 4)
        r_drill = round(drill / 2, 4)

        # Outer copper ring — stores od and drill for push_to_kicad()
        board.add_element('circle', {
            'id':          f"{prefix}-via-{i}",
            'cx':          str(cx),
            'cy':          str(cy),
            'r':           str(r_outer),
            'fill':        VIA_FILL,
            'stroke':      'none',
            'data-net':    via['net'],
            'data-via':    '1',
            'data-od':     str(od),
            'data-drill':  str(drill),
        })
        # Drill hole
        board.add_element('circle', {
            'cx':   str(cx),
            'cy':   str(cy),
            'r':    str(r_drill),
            'fill': VIA_DRILL_FILL,
        })
        count += 1

    print(f"  ses_to_svg: {len(wires)} wires, {len(vias)} vias")
    return count


def ses_file_to_svg(ses_path: str, board: BoardSVG, **kwargs) -> int:
    """Read SES from file and import into board overlays."""
    with open(ses_path, 'r') as f:
        return ses_to_svg(f.read(), board, **kwargs)