#!/usr/bin/env python3
"""
gen_pcb_from_bloom.py — Generate a .kicad_pcb from a .bloom file

Reads footprint geometry from KiCad .kicad_mod files specified in the bloom file.

Usage:
    python3 gen_pcb_from_bloom.py design.bloom -o output.kicad_pcb -f /path/to/kicad/footprints
"""

import json
import math
import sys
import uuid
import argparse
import re
from pathlib import Path

VIEWPORT_X = 120.0
VIEWPORT_Y = 65.0


def bpc(x, y):
    """Board-space to KiCad PCB coordinate."""
    return round(x + VIEWPORT_X, 4), round(y + VIEWPORT_Y, 4)


def uid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# S-expression tokenizer and parser for .kicad_mod files
# ---------------------------------------------------------------------------

def tokenize_sexpr(text):
    """Tokenize s-expression text into a list of tokens."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in ' \t\n\r':
            i += 1
        elif c == '(':
            tokens.append('(')
            i += 1
        elif c == ')':
            tokens.append(')')
            i += 1
        elif c == '"':
            # Quoted string
            j = i + 1
            while j < len(text) and text[j] != '"':
                if text[j] == '\\':
                    j += 2
                else:
                    j += 1
            tokens.append(text[i+1:j])
            i = j + 1
        else:
            # Unquoted token
            j = i
            while j < len(text) and text[j] not in ' \t\n\r()':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_sexpr(tokens, idx=0):
    """Parse tokens into nested lists. Returns (parsed, next_idx)."""
    if tokens[idx] == '(':
        result = []
        idx += 1
        while tokens[idx] != ')':
            item, idx = parse_sexpr(tokens, idx)
            result.append(item)
        return result, idx + 1
    else:
        return tokens[idx], idx + 1


def parse_kicad_mod(filepath):
    """Parse a .kicad_mod file and extract pad/courtyard/body info."""
    text = Path(filepath).read_text(encoding='utf-8')
    tokens = tokenize_sexpr(text)
    parsed, _ = parse_sexpr(tokens)

    result = {
        'lib': str(filepath),
        'pads': [],
        'courtyard': None,
        'body': None,
    }

    # Extract footprint name for lib reference
    if parsed[0] == 'footprint':
        result['lib'] = parsed[1]

    # Collect courtyard and body line endpoints for bounding box
    crtyd_points = []
    fab_points = []

    for item in parsed:
        if not isinstance(item, list):
            continue

        # Parse pad entries
        if item[0] == 'pad':
            pad = parse_pad(item)
            if pad:
                result['pads'].append(pad)

        # Collect courtyard points (F.CrtYd layer)
        elif item[0] == 'fp_line':
            layer = get_nested_value(item, 'layer')
            if layer == 'F.CrtYd':
                start = get_nested_value(item, 'start')
                end = get_nested_value(item, 'end')
                if start:
                    crtyd_points.append((float(start[0]), float(start[1])))
                if end:
                    crtyd_points.append((float(end[0]), float(end[1])))
            elif layer == 'F.Fab':
                start = get_nested_value(item, 'start')
                end = get_nested_value(item, 'end')
                if start:
                    fab_points.append((float(start[0]), float(start[1])))
                if end:
                    fab_points.append((float(end[0]), float(end[1])))

        elif item[0] == 'fp_rect':
            layer = get_nested_value(item, 'layer')
            start = get_nested_value(item, 'start')
            end = get_nested_value(item, 'end')
            points = []
            if start:
                points.append((float(start[0]), float(start[1])))
            if end:
                points.append((float(end[0]), float(end[1])))
            if layer == 'F.CrtYd':
                crtyd_points.extend(points)
            elif layer == 'F.Fab':
                fab_points.extend(points)

        elif item[0] == 'fp_poly':
            layer = get_nested_value(item, 'layer')
            pts = get_nested_value(item, 'pts')
            if pts:
                points = []
                for pt in pts:
                    if isinstance(pt, list) and pt[0] == 'xy':
                        points.append((float(pt[1]), float(pt[2])))
                if layer == 'F.CrtYd':
                    crtyd_points.extend(points)
                elif layer == 'F.Fab':
                    fab_points.extend(points)

    # Compute bounding boxes
    if crtyd_points:
        xs = [p[0] for p in crtyd_points]
        ys = [p[1] for p in crtyd_points]
        result['courtyard'] = [min(xs), min(ys), max(xs), max(ys)]

    if fab_points:
        xs = [p[0] for p in fab_points]
        ys = [p[1] for p in fab_points]
        result['body'] = [min(xs), min(ys), max(xs), max(ys)]

    # Fallback: compute from pad positions if no courtyard/body
    if result['pads']:
        pad_bounds = compute_pad_bounds(result['pads'])
        if not result['courtyard']:
            result['courtyard'] = pad_bounds
        if not result['body']:
            # Shrink slightly for body
            result['body'] = [
                pad_bounds[0] + 0.2, pad_bounds[1] + 0.2,
                pad_bounds[2] - 0.2, pad_bounds[3] - 0.2
            ]

    # Calculate centre offset: KiCad origin vs geometric pad centre
    # Bloom assumes body-centre positioning, but KiCad footprint origin may differ
    if result['pads']:
        xs = [p['at'][0] for p in result['pads']]
        ys = [p['at'][1] for p in result['pads']]
        pad_centre_x = (min(xs) + max(xs)) / 2
        pad_centre_y = (min(ys) + max(ys)) / 2
        result['centre_offset'] = (pad_centre_x, pad_centre_y)
    else:
        result['centre_offset'] = (0, 0)

    return result


def get_nested_value(lst, key):
    """Find a nested list starting with key and return its contents."""
    for item in lst:
        if isinstance(item, list) and len(item) > 0 and item[0] == key:
            return item[1:] if len(item) > 1 else []
    return None


def parse_pad(item):
    """Parse a pad s-expression into a dict."""
    if len(item) < 4:
        return None

    pad = {
        'num': item[1],
        'type': item[2],  # smd or thru_hole
        'shape': item[3],  # roundrect, rect, circle, oval
    }

    # Parse nested attributes
    at = get_nested_value(item, 'at')
    if at:
        pad['at'] = [float(at[0]), float(at[1])]
    else:
        return None

    size = get_nested_value(item, 'size')
    if size:
        pad['size'] = [float(size[0]), float(size[1])]
    else:
        return None

    layers = get_nested_value(item, 'layers')
    if layers:
        # Convert layer list to our format
        layer_str = ' '.join(f'"{l}"' if isinstance(l, str) else str(l) for l in layers)
        if 'F.Cu' in layer_str and 'B.Cu' not in layer_str and '*.Cu' not in layer_str:
            pad['layers'] = 'F'
        elif 'B.Cu' in layer_str and 'F.Cu' not in layer_str and '*.Cu' not in layer_str:
            pad['layers'] = 'B'
        else:
            pad['layers'] = '*.Cu'
    else:
        pad['layers'] = 'F'

    drill = get_nested_value(item, 'drill')
    if drill:
        pad['drill'] = float(drill[0])

    rratio = get_nested_value(item, 'roundrect_rratio')
    if rratio:
        pad['roundrect_rratio'] = float(rratio[0])

    return pad


def compute_pad_bounds(pads):
    """Compute bounding box from pad positions and sizes."""
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    for pad in pads:
        x, y = pad['at']
        w, h = pad['size']
        min_x = min(min_x, x - w/2)
        max_x = max(max_x, x + w/2)
        min_y = min(min_y, y - h/2)
        max_y = max(max_y, y + h/2)
    margin = 0.25
    return [min_x - margin, min_y - margin, max_x + margin, max_y + margin]


# ---------------------------------------------------------------------------
# S-expression generators
# ---------------------------------------------------------------------------

def gen_pad(pad, fp_rotation=0, net_num=0, net_name=""):
    """Generate a pad s-expression."""
    px, py = pad["at"]
    sw, sh = pad["size"]
    ptype = pad["type"]
    shape = pad["shape"]
    layers = pad["layers"]

    if layers == "F":
        layer_str = '"F.Cu" "F.Paste" "F.Mask"'
    elif layers == "B":
        layer_str = '"B.Cu" "B.Paste" "B.Mask"'
    else:
        layer_str = '"*.Cu" "*.Mask"'

    rot_str = f" {fp_rotation}" if fp_rotation != 0 else ""

    lines = []
    lines.append(f'\t\t(pad "{pad["num"]}" {ptype} {shape}')
    lines.append(f'\t\t\t(at {px} {py}{rot_str})')
    lines.append(f'\t\t\t(size {sw} {sh})')
    if "drill" in pad:
        lines.append(f'\t\t\t(drill {pad["drill"]})')
    if shape == "roundrect":
        rratio = pad.get('roundrect_rratio', 0.25)
        lines.append(f'\t\t\t(roundrect_rratio {rratio})')
    lines.append(f'\t\t\t(layers {layer_str})')
    if net_num > 0:
        lines.append(f'\t\t\t(net {net_num} "{net_name}")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t)')
    return "\n".join(lines)


def gen_footprint(ref, comp, fp_def, net_map, board_x, board_y, rotation=0):
    """Generate a complete footprint s-expression."""
    if not fp_def or not fp_def.get('pads'):
        print(f"  WARNING: no footprint data for {ref}, skipping", file=sys.stderr)
        return ""

    lib = fp_def["lib"]

    # Apply centre offset: KiCad origin may not be at pad centre
    # Bloom positions are body-centre, so we subtract the offset
    cx_off, cy_off = fp_def.get('centre_offset', (0, 0))

    # Rotate offset if component is rotated (clockwise rotation)
    rad = math.radians(rotation)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    rx_off = cx_off * cos_r + cy_off * sin_r
    ry_off = -cx_off * sin_r + cy_off * cos_r

    px, py = bpc(board_x - rx_off, board_y - ry_off)
    rot_str = f" {rotation}" if rotation != 0 else ""

    # Build pad net assignments from component pins
    pad_nets = {}
    for pin_num, pin_data in comp.get("pins", {}).items():
        net = pin_data.get("net", "")
        if net and net in net_map:
            pad_nets[pin_num] = (net_map[net], net)

    lines = []
    lines.append(f'\t(footprint "{lib}"')
    lines.append(f'\t\t(layer "F.Cu")')
    lines.append(f'\t\t(uuid "{uid()}")')
    lines.append(f'\t\t(at {px} {py}{rot_str})')

    # Reference property
    crtyd = fp_def.get('courtyard', [-1, -1, 1, 1])
    lines.append(f'\t\t(property "Reference" "{ref}"')
    lines.append(f'\t\t\t(at 0 {crtyd[1] - 1}{rot_str})')
    lines.append(f'\t\t\t(layer "F.SilkS")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append(f'\t\t)')

    # Value property
    value = comp.get("value", comp.get("type", ""))
    lines.append(f'\t\t(property "Value" "{value}"')
    lines.append(f'\t\t\t(at 0 {crtyd[3] + 1}{rot_str})')
    lines.append(f'\t\t\t(layer "F.Fab")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append(f'\t\t)')

    # Courtyard
    if crtyd:
        cx1, cy1, cx2, cy2 = crtyd
        lines.append(f'\t\t(fp_rect')
        lines.append(f'\t\t\t(start {cx1} {cy1})')
        lines.append(f'\t\t\t(end {cx2} {cy2})')
        lines.append(f'\t\t\t(stroke (width 0.05) (type solid))')
        lines.append(f'\t\t\t(fill none)')
        lines.append(f'\t\t\t(layer "F.CrtYd")')
        lines.append(f'\t\t\t(uuid "{uid()}")')
        lines.append(f'\t\t)')

    # Body outline on Fab
    body = fp_def.get('body')
    if body:
        bx1, by1, bx2, by2 = body
        lines.append(f'\t\t(fp_rect')
        lines.append(f'\t\t\t(start {bx1} {by1})')
        lines.append(f'\t\t\t(end {bx2} {by2})')
        lines.append(f'\t\t\t(stroke (width 0.1) (type solid))')
        lines.append(f'\t\t\t(fill none)')
        lines.append(f'\t\t\t(layer "F.Fab")')
        lines.append(f'\t\t\t(uuid "{uid()}")')
        lines.append(f'\t\t)')

    # Pads
    for pad in fp_def["pads"]:
        pn = pad["num"]
        net_num, net_name = pad_nets.get(pn, (0, ""))
        lines.append(gen_pad(pad, rotation, net_num, net_name))

    lines.append(f'\t)')
    return "\n".join(lines)


def gen_board_outline(w, h):
    x1, y1 = bpc(0, 0)
    x2, y2 = bpc(w, h)
    return (
        f'\t(gr_rect\n'
        f'\t\t(start {x1} {y1})\n'
        f'\t\t(end {x2} {y2})\n'
        f'\t\t(stroke\n'
        f'\t\t\t(width 0.05)\n'
        f'\t\t\t(type solid)\n'
        f'\t\t)\n'
        f'\t\t(fill none)\n'
        f'\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{uid()}")\n'
        f'\t)'
    )


def gen_track(track, net_map):
    """Generate a track/segment s-expression."""
    x1, y1 = bpc(track["x1"], track["y1"])
    x2, y2 = bpc(track["x2"], track["y2"])
    width = track.get("width", 0.25)
    layer = track.get("layer", "F.Cu")
    net = track.get("net", "")
    net_num = net_map.get(net, 0)

    return (
        f'\t(segment\n'
        f'\t\t(start {x1} {y1})\n'
        f'\t\t(end {x2} {y2})\n'
        f'\t\t(width {width})\n'
        f'\t\t(layer "{layer}")\n'
        f'\t\t(net {net_num})\n'
        f'\t\t(uuid "{uid()}")\n'
        f'\t)'
    )


def gen_via(via, net_map):
    """Generate a via s-expression."""
    x, y = bpc(via["x"], via["y"])
    size = via.get("od", 0.6)  # outer diameter
    drill = via.get("id", 0.3)  # inner diameter / drill
    net = via.get("net", "")
    net_num = net_map.get(net, 0)

    return (
        f'\t(via\n'
        f'\t\t(at {x} {y})\n'
        f'\t\t(size {size})\n'
        f'\t\t(drill {drill})\n'
        f'\t\t(layers "F.Cu" "B.Cu")\n'
        f'\t\t(net {net_num})\n'
        f'\t\t(uuid "{uid()}")\n'
        f'\t)'
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_footprint_lib(bloom, footprints_path):
    """Load footprint definitions from .kicad_mod files specified in bloom."""
    fp_lib = {}
    pcb_footprints = bloom.get('pcb', {}).get('footprints', {})

    for package, fp_info in pcb_footprints.items():
        kicad_mod = fp_info.get('kicad_mod')
        if not kicad_mod:
            print(f"  WARNING: no kicad_mod path for package '{package}'", file=sys.stderr)
            continue

        mod_path = Path(footprints_path) / kicad_mod
        if not mod_path.exists():
            print(f"  WARNING: footprint file not found: {mod_path}", file=sys.stderr)
            continue

        try:
            fp_def = parse_kicad_mod(mod_path)
            fp_lib[package] = fp_def
            print(f"  Loaded: {package} <- {kicad_mod} ({len(fp_def['pads'])} pads)", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR parsing {mod_path}: {e}", file=sys.stderr)

    return fp_lib


def generate_pcb(bloom, footprint_lib):
    components = bloom.get("components", {})
    pcb = bloom.get("pcb", {})
    board = pcb.get("board", [50, 40])
    rules = pcb.get("rules", {})

    clearance = rules.get("clearance", 0.2)
    track_w = rules.get("track", 0.25)
    via_drill = rules.get("via_drill", 0.3)
    via_annular = rules.get("via_annular", 0.15)
    via_size = round(via_drill + 2 * via_annular, 3)

    # Collect all nets
    all_nets = set()
    for comp in components.values():
        for pin in comp.get("pins", {}).values():
            net = pin.get("net", "")
            if net:
                all_nets.add(net)
    net_list = sorted(all_nets)
    net_map = {name: i + 1 for i, name in enumerate(net_list)}

    # PCB positions — resolve from placement grid (col/row at 1mm scale)
    placement = bloom.get("placement", {})
    positions = {}
    for ref, p in placement.items():
        # Centre position: col + w/2, row + h/2 (SCALE = 1.0mm)
        x = p["col"] + p["w"] / 2
        y = p["row"] + p["h"] / 2
        positions[ref] = (x, y)

    # Header
    lines = []
    lines.append('(kicad_pcb\n'
                  '\t(version 20240108)\n'
                  '\t(generator "gen_pcb_from_bloom")\n'
                  '\t(generator_version "1.0")\n'
                  '\t(general\n'
                  '\t\t(thickness 1.6)\n'
                  '\t\t(legacy_teardrops no)\n'
                  '\t)\n'
                  '\t(paper "A4")')

    # Layers
    lines.append('\t(layers\n'
                  '\t\t(0 "F.Cu" signal)\n'
                  '\t\t(31 "B.Cu" signal)\n'
                  '\t\t(32 "B.Adhes" user "B.Adhesive")\n'
                  '\t\t(33 "F.Adhes" user "F.Adhesive")\n'
                  '\t\t(34 "B.Paste" user)\n'
                  '\t\t(35 "F.Paste" user)\n'
                  '\t\t(36 "B.SilkS" user "B.Silkscreen")\n'
                  '\t\t(37 "F.SilkS" user "F.Silkscreen")\n'
                  '\t\t(38 "B.Mask" user "B.Mask")\n'
                  '\t\t(39 "F.Mask" user "F.Mask")\n'
                  '\t\t(40 "Dwgs.User" user "User.Drawings")\n'
                  '\t\t(41 "Cmts.User" user "User.Comments")\n'
                  '\t\t(42 "Eco1.User" user "User.Eco1")\n'
                  '\t\t(43 "Eco2.User" user "User.Eco2")\n'
                  '\t\t(44 "Edge.Cuts" user)\n'
                  '\t\t(45 "Margin" user)\n'
                  '\t\t(46 "B.CrtYd" user "B.Courtyard")\n'
                  '\t\t(47 "F.CrtYd" user "F.Courtyard")\n'
                  '\t\t(48 "B.Fab" user "B.Fabrication")\n'
                  '\t\t(49 "F.Fab" user "F.Fabrication")\n'
                  '\t)')

    # Setup
    lines.append(f'\t(setup\n'
                 f'\t\t(pad_to_mask_clearance 0.05)\n'
                 f'\t\t(allow_soldermask_bridges_in_footprints no)\n'
                 f'\t\t(pcbplotparams\n'
                 f'\t\t\t(layerselection 0x00010fc_ffffffff)\n'
                 f'\t\t\t(plot_on_all_layers_selection 0x0000000_00000000)\n'
                 f'\t\t)\n'
                 f'\t)')

    # Nets
    lines.append(f'\t(net 0 "")')
    for name, num in sorted(net_map.items(), key=lambda x: x[1]):
        lines.append(f'\t(net {num} "{name}")')

    # Footprints
    for ref, comp in components.items():
        package = comp.get("package", "")
        fp_def = footprint_lib.get(package)
        if not fp_def:
            print(f"  WARNING: no footprint for package '{package}', skipping {ref}",
                  file=sys.stderr)
            continue
        bx, by = positions.get(ref, (0, 0))
        rotation = comp.get("rotation", 0)
        fp_text = gen_footprint(ref, comp, fp_def, net_map, bx, by, rotation)
        if fp_text:
            lines.append(fp_text)

    # Tracks
    tracks = pcb.get("tracks", [])
    for track in tracks:
        lines.append(gen_track(track, net_map))

    # Vias
    vias = pcb.get("vias", [])
    for via in vias:
        lines.append(gen_via(via, net_map))

    # Board outline
    lines.append(gen_board_outline(board[0], board[1]))

    lines.append(')')
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate .kicad_pcb from .bloom")
    parser.add_argument("bloom", help="Input .bloom file")
    parser.add_argument("-o", "--output", required=True, help="Output .kicad_pcb")
    parser.add_argument("-f", "--footprints", required=True,
                        help="Path to KiCad footprints folder")
    args = parser.parse_args()

    bloom_path = Path(args.bloom)
    if not bloom_path.exists():
        print(f"Error: {bloom_path} not found", file=sys.stderr)
        sys.exit(1)

    footprints_path = Path(args.footprints)
    if not footprints_path.exists():
        print(f"Error: footprints path {footprints_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(bloom_path) as f:
        bloom = json.load(f)

    print(f"Loading footprints from {footprints_path}...", file=sys.stderr)
    footprint_lib = load_footprint_lib(bloom, footprints_path)

    pcb_text = generate_pcb(bloom, footprint_lib)
    Path(args.output).write_text(pcb_text, encoding='utf-8')

    n_comp = len(bloom.get("components", {}))
    n_fp = len(footprint_lib)
    n_tracks = len(bloom.get("pcb", {}).get("tracks", []))
    n_vias = len(bloom.get("pcb", {}).get("vias", []))
    print(f"Generated: {args.output}", file=sys.stderr)
    print(f"  {n_comp} components, {n_fp} footprints, {n_tracks} tracks, {n_vias} vias", file=sys.stderr)


if __name__ == "__main__":
    main()
