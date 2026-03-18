#!/usr/bin/env python3
"""
distill_pcb.py - Convert .kicad_pcb to compact .dpcb format

Extracts only layout-relevant information:
- Board dimensions (from Edge.Cuts or bounding box)
- Layer count
- Net definitions (net name -> pad assignments)
- Footprint placements (ref, library, position, rotation, layer)
- Tracks (endpoints, width, layer, net)
- Vias (position, drill/annular, net)
- Zones (net, layer, outline)

Discards: graphics, silkscreen, courtyard, 3D models, UUIDs,
pad geometry, fab layers, setup/plot parameters.

Usage:
    python3 distill_pcb.py input.kicad_pcb [-o output.dpcb]
"""

import sys
import re
import argparse
from pathlib import Path

# Viewport offset — must match gen_pcb.py
# Coordinates in .dpcb are board-relative (0,0 = board corner)
# gen_pcb.py adds this offset when writing to .kicad_pcb
VIEWPORT_X = 120.0
VIEWPORT_Y = 65.0


def pcb_to_board(x, y):
    """Convert absolute KiCad coordinates to board-relative coordinates."""
    return round(x - VIEWPORT_X, 4), round(y - VIEWPORT_Y, 4)


def tokenize(text):
    """Simple s-expression tokenizer."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in '()':
            tokens.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                if text[j] == '\\':
                    j += 1
                j += 1
            tokens.append(text[i:j+1])
            i = j + 1
        elif c in ' \t\n\r':
            i += 1
        else:
            j = i
            while j < len(text) and text[j] not in '() \t\n\r"':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_sexpr(tokens, idx=0):
    """Parse s-expression tokens into nested lists."""
    if tokens[idx] == '(':
        lst = []
        idx += 1
        while tokens[idx] != ')':
            item, idx = parse_sexpr(tokens, idx)
            lst.append(item)
        return lst, idx + 1
    else:
        return tokens[idx], idx + 1


def parse_file(text):
    """Parse entire file into s-expression tree."""
    tokens = tokenize(text)
    result = []
    idx = 0
    while idx < len(tokens):
        item, idx = parse_sexpr(tokens, idx)
        result.append(item)
    return result[0] if len(result) == 1 else result


def find_nodes(tree, name):
    """Find all child nodes with given name."""
    results = []
    if isinstance(tree, list):
        for item in tree:
            if isinstance(item, list) and len(item) > 0 and item[0] == name:
                results.append(item)
    return results


def find_node(tree, name):
    """Find first child node with given name."""
    nodes = find_nodes(tree, name)
    return nodes[0] if nodes else None


def unquote(s):
    """Remove surrounding quotes from a string."""
    if isinstance(s, str) and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def extract_nets(tree):
    """Extract net definitions: {net_number: net_name}"""
    nets = {}
    for node in find_nodes(tree, 'net'):
        if len(node) >= 3:
            net_num = int(node[1])
            net_name = unquote(node[2])
            if net_name:  # skip net 0 ""
                nets[net_num] = net_name
    return nets


def extract_footprints(tree, nets):
    """Extract footprint placements and their pad-to-net assignments."""
    footprints = []
    pad_nets = {}  # {net_name: [(ref, pad_num), ...]}

    for fp_node in find_nodes(tree, 'footprint'):
        fp_lib = unquote(fp_node[1])
        layer_node = find_node(fp_node, 'layer')
        layer = unquote(layer_node[1]) if layer_node else "F.Cu"

        at_node = find_node(fp_node, 'at')
        abs_x = float(at_node[1])
        abs_y = float(at_node[2])
        x, y = pcb_to_board(abs_x, abs_y)
        rotation = float(at_node[3]) if len(at_node) > 3 else 0

        # Get reference
        ref = "?"
        ref_node = find_node(fp_node, 'property')
        for prop in find_nodes(fp_node, 'property'):
            if len(prop) >= 3 and unquote(prop[1]) == "Reference":
                ref = unquote(prop[2])
                break

        # Get value
        value = ""
        for prop in find_nodes(fp_node, 'property'):
            if len(prop) >= 3 and unquote(prop[1]) == "Value":
                value = unquote(prop[2])
                break

        # Extract pad offsets
        pads = {}
        for pad_node in find_nodes(fp_node, 'pad'):
            pad_num = unquote(pad_node[1])
            pad_at = find_node(pad_node, 'at')
            if pad_at:
                pad_x = float(pad_at[1])
                pad_y = float(pad_at[2])
                pads[pad_num] = (pad_x, pad_y)

        footprints.append({
            'ref': ref,
            'lib': fp_lib,
            'x': x,
            'y': y,
            'rotation': rotation,
            'layer': layer,
            'value': value,
            'pads': pads,
        })

        # Extract pad net assignments
        for pad_node in find_nodes(fp_node, 'pad'):
            pad_num = unquote(pad_node[1])
            pad_net_node = find_node(pad_node, 'net')
            if pad_net_node and len(pad_net_node) >= 3:
                net_name = unquote(pad_net_node[2])
                if net_name:
                    if net_name not in pad_nets:
                        pad_nets[net_name] = []
                    pad_nets[net_name].append((ref, pad_num))

    return footprints, pad_nets


def extract_tracks(tree, nets):
    """Extract track segments."""
    tracks = []
    for seg_node in find_nodes(tree, 'segment'):
        start_node = find_node(seg_node, 'start')
        end_node = find_node(seg_node, 'end')
        width_node = find_node(seg_node, 'width')
        layer_node = find_node(seg_node, 'layer')
        net_node = find_node(seg_node, 'net')

        if start_node and end_node:
            x1, y1 = pcb_to_board(float(start_node[1]), float(start_node[2]))
            x2, y2 = pcb_to_board(float(end_node[1]), float(end_node[2]))
            width = float(width_node[1]) if width_node else 0.25
            layer = unquote(layer_node[1]) if layer_node else "F.Cu"
            net_num = int(net_node[1]) if net_node else 0
            net_name = nets.get(net_num, "")

            tracks.append({
                'x1': x1, 'y1': y1,
                'x2': x2, 'y2': y2,
                'width': width,
                'layer': layer,
                'net': net_name,
            })

    return tracks


def extract_vias(tree, nets):
    """Extract vias."""
    vias = []
    for via_node in find_nodes(tree, 'via'):
        at_node = find_node(via_node, 'at')
        size_node = find_node(via_node, 'size')
        drill_node = find_node(via_node, 'drill')
        net_node = find_node(via_node, 'net')

        if at_node:
            x, y = pcb_to_board(float(at_node[1]), float(at_node[2]))
            size = float(size_node[1]) if size_node else 0.6
            drill = float(drill_node[1]) if drill_node else 0.3
            net_num = int(net_node[1]) if net_node else 0
            net_name = nets.get(net_num, "")
            annular = round((size - drill) / 2, 3)

            vias.append({
                'x': x, 'y': y,
                'drill': drill,
                'annular': annular,
                'net': net_name,
            })

    return vias


def extract_zones(tree, nets):
    """Extract copper zones (outline only, not fill)."""
    zones = []
    for zone_node in find_nodes(tree, 'zone'):
        net_node = find_node(zone_node, 'net')
        net_name_node = find_node(zone_node, 'net_name')
        layer_node = find_node(zone_node, 'layer')
        # Also check for 'layers' (multi-layer zones)
        layers_node = find_node(zone_node, 'layers')

        net_name = unquote(net_name_node[1]) if net_name_node else ""
        layer = unquote(layer_node[1]) if layer_node else ""

        # Find polygon outline
        polygon_node = find_node(zone_node, 'polygon')
        if polygon_node:
            pts_node = find_node(polygon_node, 'pts')
            if pts_node:
                points = []
                for xy_node in find_nodes(pts_node, 'xy'):
                    px, py = pcb_to_board(float(xy_node[1]), float(xy_node[2]))
                    points.append((px, py))
                if points:
                    zones.append({
                        'net': net_name,
                        'layer': layer,
                        'points': points,
                    })

    return zones


def extract_board_outline(tree):
    """Try to find board outline from Edge.Cuts layer graphics."""
    # Look for gr_rect or gr_line on Edge.Cuts
    lines = []
    for node in find_nodes(tree, 'gr_rect'):
        layer_node = find_node(node, 'layer')
        if layer_node and unquote(layer_node[1]) == "Edge.Cuts":
            start_node = find_node(node, 'start')
            end_node = find_node(node, 'end')
            if start_node and end_node:
                x1, y1 = float(start_node[1]), float(start_node[2])
                x2, y2 = float(end_node[1]), float(end_node[2])
                w = round(abs(x2 - x1), 2)
                h = round(abs(y2 - y1), 2)
                return w, h

    for node in find_nodes(tree, 'gr_line'):
        layer_node = find_node(node, 'layer')
        if layer_node and unquote(layer_node[1]) == "Edge.Cuts":
            start_node = find_node(node, 'start')
            end_node = find_node(node, 'end')
            if start_node and end_node:
                lines.append((
                    float(start_node[1]), float(start_node[2]),
                    float(end_node[1]), float(end_node[2])
                ))

    # If we have Edge.Cuts lines, compute bounding box
    if lines:
        all_x = [l[0] for l in lines] + [l[2] for l in lines]
        all_y = [l[1] for l in lines] + [l[3] for l in lines]
        w = round(max(all_x) - min(all_x), 2)
        h = round(max(all_y) - min(all_y), 2)
        return w, h

    return None, None


def count_layers(tree):
    """Count copper layers."""
    layers_node = find_node(tree, 'layers')
    if not layers_node:
        return 2
    count = 0
    for item in layers_node[1:]:
        if isinstance(item, list) and len(item) >= 3:
            layer_name = unquote(item[1])
            layer_type = unquote(item[2])
            if layer_type == "signal":
                count += 1
    return count if count > 0 else 2


def format_dpcb(board_w, board_h, layer_count, footprints, pad_nets, tracks, vias, zones):
    """Format extracted data as .dpcb text."""
    lines = []

    # Header
    lines.append("HDR:v1:gen=distill_pcb")
    if board_w and board_h:
        lines.append(f"BOARD:{board_w}x{board_h}")
    else:
        lines.append("# BOARD: no Edge.Cuts outline defined")
    lines.append(f"LAYERS:{layer_count}")
    lines.append("RULES:clearance=0.2:track=0.25:via=0.6/0.3")
    lines.append("")

    # Footprints
    lines.append("# Footprints")
    for fp in sorted(footprints, key=lambda f: f['ref']):
        rot_str = f":r{int(fp['rotation'])}" if fp['rotation'] != 0 else ""
        layer_str = f":{fp['layer']}" if fp['layer'] != "F.Cu" else ""
        val_comment = f"  # {fp['value']}" if fp['value'] else ""
        lines.append(f"FP:{fp['ref']}:{fp['lib']}@({fp['x']},{fp['y']}){rot_str}{layer_str}{val_comment}")
    lines.append("")

    # Pads — one entry per unique footprint library, showing pad offsets from origin
    lines.append("# Pads (offset from footprint origin)")
    seen_libs = {}
    for fp in sorted(footprints, key=lambda f: f['ref']):
        if fp['lib'] not in seen_libs:
            seen_libs[fp['lib']] = fp['pads']
    for lib in sorted(seen_libs.keys()):
        pads = seen_libs[lib]
        pad_strs = [f"{num}@({pads[num][0]},{pads[num][1]})" for num in sorted(pads.keys(), key=lambda n: int(n) if n.isdigit() else n)]
        lines.append(f"PADS:{lib}:{','.join(pad_strs)}")
    lines.append("")

    # Nets
    lines.append("# Nets")
    for net_name in sorted(pad_nets.keys()):
        pads = pad_nets[net_name]
        pad_strs = [f"{ref}.{pad}" for ref, pad in sorted(pads)]
        lines.append(f"NET:{net_name}:{','.join(pad_strs)}")
    lines.append("")

    # Tracks
    if tracks:
        lines.append("# Tracks")
        for t in tracks:
            lines.append(f"TRK:({t['x1']},{t['y1']})->({t['x2']},{t['y2']}):{t['width']}:{t['layer']}:{t['net']}")
        lines.append("")

    # Vias
    if vias:
        lines.append("# Vias")
        for v in vias:
            lines.append(f"VIA:({v['x']},{v['y']}):{v['drill']}/{v['annular']}:{v['net']}")
        lines.append("")

    # Zones
    if zones:
        lines.append("# Zones")
        for z in zones:
            pts_str = "".join(f"({p[0]},{p[1]})" for p in z['points'])
            lines.append(f"ZONE:{z['net']}:{z['layer']}:{pts_str}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Distill .kicad_pcb to compact .dpcb format")
    parser.add_argument("input", help="Input .kicad_pcb file")
    parser.add_argument("-o", "--output", help="Output .dpcb file (default: stdout)")
    args = parser.parse_args()

    # Read input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    text = input_path.read_text(encoding='utf-8')

    # Parse
    tree = parse_file(text)

    # Extract
    nets = extract_nets(tree)
    layer_count = count_layers(tree)
    board_w, board_h = extract_board_outline(tree)
    footprints, pad_nets = extract_footprints(tree, nets)
    tracks = extract_tracks(tree, nets)
    vias = extract_vias(tree, nets)
    zones = extract_zones(tree, nets)

    # Format
    output = format_dpcb(board_w, board_h, layer_count, footprints, pad_nets, tracks, vias, zones)

    # Write
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        print(f"Distilled {input_path} -> {args.output}", file=sys.stderr)
        print(f"  {len(footprints)} footprints, {len(pad_nets)} nets, {len(tracks)} tracks, {len(vias)} vias, {len(zones)} zones", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()