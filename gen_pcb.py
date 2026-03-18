#!/usr/bin/env python3
"""
gen_pcb.py - Patch a .kicad_pcb file with layout changes from a .dpcb file

Takes an existing .kicad_pcb (with full footprint geometry) and applies
position/rotation changes, track additions, via additions, and zone
additions from a .dpcb file.

The .kicad_pcb provides all the verbose geometry (pad shapes, silkscreen,
courtyard, etc). The .dpcb provides the layout intent (where things go,
how they're routed).

BOARD-FIRST PHILOSOPHY:
    BOARD:WxH in the .dpcb is the PRIMARY input. The Edge.Cuts rectangle
    is always written at exactly WxH, anchored at VIEWPORT_OFFSET so the
    board appears in KiCad's default view. Component coordinates in the
    .dpcb are in board space (0,0 = board corner); this script adds
    VIEWPORT_OFFSET when writing to .kicad_pcb.

    The Edge.Cuts outline is ALWAYS regenerated — existing Edge.Cuts
    content is removed and replaced on every iteration.

Usage:
    python3 gen_pcb.py base.kicad_pcb design.dpcb -o output.kicad_pcb

Iteration workflow:
    python3 gen_pcb.py test3_000.kicad_pcb test3.dpcb -o test3_001.kicad_pcb
    python3 gen_pcb.py test3_001.kicad_pcb test3.dpcb -o test3_002.kicad_pcb
"""

import sys
import re
import argparse
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Viewport offset — added to all coordinates when writing .kicad_pcb
# Positions the board in KiCad's default view window.
# Components in .dpcb use board-space (0,0 = board corner).
# ---------------------------------------------------------------------------
VIEWPORT_X = 120.0
VIEWPORT_Y = 65.0


def board_to_pcb(x, y):
    """Convert board-space coordinate to .kicad_pcb coordinate."""
    return round(x + VIEWPORT_X, 4), round(y + VIEWPORT_Y, 4)


# ---------------------------------------------------------------------------
# .dpcb parser
# ---------------------------------------------------------------------------

def parse_dpcb(text):
    """Parse a .dpcb file into structured data."""
    result = {
        'footprints': {},   # ref -> {lib, x, y, rotation, layer}
        'nets': {},         # net_name -> [(ref, pad), ...]
        'tracks': [],       # [{x1, y1, x2, y2, width, layer, net}, ...]
        'vias': [],         # [{x, y, drill, annular, net}, ...]
        'zones': [],        # [{net, layer, points}, ...]
        'board': None,      # (width, height) or None
        'layers': 2,
        'rules': {},
    }

    for line in text.strip().split('\n'):
        line = line.split('#')[0].strip()
        if not line:
            continue

        if line.startswith('FP:'):
            fp = parse_fp_line(line)
            if fp:
                result['footprints'][fp['ref']] = fp

        elif line.startswith('NET:'):
            net_name, pads = parse_net_line(line)
            if net_name:
                result['nets'][net_name] = pads

        elif line.startswith('TRK:'):
            trk = parse_trk_line(line)
            if trk:
                result['tracks'].append(trk)

        elif line.startswith('VIA:'):
            via = parse_via_line(line)
            if via:
                result['vias'].append(via)

        elif line.startswith('ZONE:'):
            zone = parse_zone_line(line)
            if zone:
                result['zones'].append(zone)

        elif line.startswith('BOARD:'):
            m = re.match(r'BOARD:([\d.]+)x([\d.]+)', line)
            if m:
                result['board'] = (float(m.group(1)), float(m.group(2)))

        elif line.startswith('LAYERS:'):
            m = re.match(r'LAYERS:(\d+)', line)
            if m:
                result['layers'] = int(m.group(1))

        elif line.startswith('RULES:'):
            result['rules'] = parse_rules_line(line)

    return result


def parse_fp_line(line):
    """Parse: FP:R1:Resistor_THT:R_Axial...@(130.24,72.4)[:r90][:B.Cu]"""
    rest = line[3:]
    ref_end = rest.index(':')
    ref = rest[:ref_end]
    rest = rest[ref_end+1:]

    at_pos = rest.index('@')
    lib = rest[:at_pos]
    rest = rest[at_pos+1:]

    m = re.match(r'\(([\d.]+),([\d.]+)\)(.*)', rest)
    if not m:
        return None
    x = float(m.group(1))
    y = float(m.group(2))
    rest = m.group(3)

    rotation = 0
    m = re.match(r':r(\d+)(.*)', rest)
    if m:
        rotation = int(m.group(1))
        rest = m.group(2)

    layer = "F.Cu"
    m = re.match(r':([FB]\.Cu)(.*)', rest)
    if m:
        layer = m.group(1)

    return {
        'ref': ref,
        'lib': lib,
        'x': x,
        'y': y,
        'rotation': rotation,
        'layer': layer,
    }


def parse_net_line(line):
    """Parse: NET:VCC:C2.1,R1.1,U1.4,U1.8"""
    parts = line[4:].split(':')
    if len(parts) < 2:
        return None, None
    net_name = parts[0]
    pad_strs = parts[1].split(',')
    pads = []
    for ps in pad_strs:
        ps = ps.strip()
        if '.' in ps:
            ref, pad = ps.rsplit('.', 1)
            pads.append((ref, pad))
    return net_name, pads


def parse_trk_line(line):
    """Parse: TRK:(x1,y1)->(x2,y2):width:layer:net"""
    m = re.match(
        r'TRK:\(([\d.-]+),([\d.-]+)\)->\(([\d.-]+),([\d.-]+)\):([\d.]+):([^:]+):(.*)',
        line
    )
    if not m:
        return None
    return {
        'x1': float(m.group(1)),
        'y1': float(m.group(2)),
        'x2': float(m.group(3)),
        'y2': float(m.group(4)),
        'width': float(m.group(5)),
        'layer': m.group(6),
        'net': m.group(7),
    }


def parse_via_line(line):
    """Parse: VIA:(x,y):drill/annular:net"""
    m = re.match(
        r'VIA:\(([\d.-]+),([\d.-]+)\):([\d.]+)/([\d.]+):(.*)',
        line
    )
    if not m:
        return None
    return {
        'x': float(m.group(1)),
        'y': float(m.group(2)),
        'drill': float(m.group(3)),
        'annular': float(m.group(4)),
        'net': m.group(5),
    }


def parse_zone_line(line):
    """Parse: ZONE:GND:B.Cu:(x1,y1)(x2,y2)..."""
    m = re.match(r'ZONE:([^:]+):([^:]+):(.*)', line)
    if not m:
        return None
    net = m.group(1)
    layer = m.group(2)
    pts_str = m.group(3)
    points = re.findall(r'\(([\d.-]+),([\d.-]+)\)', pts_str)
    pts = [(float(x), float(y)) for x, y in points]
    return {'net': net, 'layer': layer, 'points': pts}


def parse_rules_line(line):
    """Parse: RULES:clearance=0.2:track=0.25:via=0.6/0.3"""
    rules = {}
    parts = line[6:].split(':')
    for part in parts:
        if '=' in part:
            key, val = part.split('=', 1)
            if key == 'via' and '/' in val:
                drill, ann = val.split('/')
                rules['via_drill'] = float(drill)
                rules['via_annular'] = float(ann)
            else:
                rules[key] = float(val)
    return rules


# ---------------------------------------------------------------------------
# .kicad_pcb text manipulation
# ---------------------------------------------------------------------------

def find_footprint_blocks(text):
    """Find all footprint blocks. Returns {ref: (start, end, block_text)}."""
    blocks = {}
    i = 0
    while i < len(text):
        idx = text.find('(footprint ', i)
        if idx == -1:
            break

        depth = 0
        j = idx
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            elif text[j] == '"':
                j += 1
                while j < len(text) and text[j] != '"':
                    if text[j] == '\\':
                        j += 1
                    j += 1
            j += 1

        block_text = text[idx:j+1]
        ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block_text)
        if ref_match:
            blocks[ref_match.group(1)] = (idx, j+1, block_text)

        i = j + 1

    return blocks


def update_footprint_position(block_text, new_x, new_y, new_rotation):
    """Update the (at x y [rotation]) in a footprint block, and sync pad rotations."""
    px, py = board_to_pcb(new_x, new_y)

    def replace_at(match):
        indent = match.group(1)
        if new_rotation != 0:
            return f'{indent}(at {px} {py} {new_rotation})'
        else:
            return f'{indent}(at {px} {py})'

    result = re.sub(r'(\n\t\t)\(at [\d.\s-]+\)', replace_at, block_text, count=1)
    if result == block_text:
        result = re.sub(r'(\s)\(at ([\d.]+) ([\d.]+)( [\d.]+)?\)',
                        replace_at, block_text, count=1)

    # Sync pad shape rotation: each pad's (at dx dy [rot]) must carry the
    # footprint rotation so KiCad renders the pad shape at the correct angle.
    # Only match 2-number (at dx dy) entries — pad offsets in the base file
    # have no rotation component. Property/text entries already have 3 numbers
    # and are left unchanged.
    def replace_pad_at(match):
        indent = match.group(1)
        dx = match.group(2)
        dy = match.group(3)
        if new_rotation != 0:
            return f'{indent}(at {dx} {dy} {new_rotation})'
        else:
            return f'{indent}(at {dx} {dy})'

    result = re.sub(
        r'(\n\t\t\t)\(at (-?[\d.]+) (-?[\d.]+)\)',
        replace_pad_at, result
    )
    return result


def update_footprint_layer(block_text, new_layer):
    """Update the (layer "...") in a footprint block."""
    return re.sub(r'\(layer "[^"]+"\)', f'(layer "{new_layer}")',
                  block_text, count=1)


def build_net_map(text):
    """Extract {net_name: net_number} from the .kicad_pcb."""
    net_map = {}
    for m in re.finditer(r'\(net\s+(\d+)\s+"([^"]*?)"\)', text):
        net_num = int(m.group(1))
        net_name = m.group(2)
        if net_name:
            net_map[net_name] = net_num
    return net_map


def remove_sexp_blocks(text, tag):
    """
    Remove all top-level s-expression blocks starting with (tag ...),
    using depth counting to handle multi-line nested blocks correctly.
    Also strips any leading whitespace/newlines before each removed block.
    """
    search = f'({tag}'
    slen = len(search)
    result = []
    i = 0
    while i < len(text):
        # Match (tag followed by any whitespace — KiCad uses newline, not space
        if (text[i:i+slen] == search
                and i + slen < len(text)
                and text[i+slen] in ' \t\n\r'):
            # Find the matching closing paren
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                elif text[j] == '"':
                    j += 1
                    while j < len(text) and text[j] != '"':
                        if text[j] == '\\':
                            j += 1
                        j += 1
                j += 1
            # Strip preceding whitespace/newlines
            while result and result[-1] in '\n\t\r ':
                result.pop()
            i = j + 1  # skip past closing paren
            continue
        result.append(text[i])
        i += 1
    return ''.join(result)


def remove_all_segments(text):
    """Remove all (segment ...) blocks, handling multi-line format."""
    return remove_sexp_blocks(text, 'segment')


def remove_all_vias(text):
    """Remove all top-level (via ...) blocks, handling multi-line format."""
    return remove_sexp_blocks(text, 'via')


def remove_all_zones(text):
    """Remove all (zone ...) blocks."""
    result = []
    i = 0
    while i < len(text):
        if text[i:i+5] == '(zone' and (i == 0 or text[i-1] in '\n\t '):
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                elif text[j] == '"':
                    j += 1
                    while j < len(text) and text[j] != '"':
                        if text[j] == '\\':
                            j += 1
                        j += 1
                j += 1
            while i > 0 and text[i-1] in '\n\t ':
                i -= 1
            i = j + 1
            continue
        result.append(text[i])
        i += 1
    return ''.join(result)


def remove_edge_cuts(text):
    """Remove all existing Edge.Cuts graphic items (gr_line, gr_rect, gr_arc)."""
    # Remove gr_rect on Edge.Cuts
    text = re.sub(
        r'\n?\t?\(gr_rect[^)]*(?:\([^)]*\))*[^)]*"Edge\.Cuts"[^)]*(?:\([^)]*\))*[^)]*\)',
        '', text
    )
    # Remove gr_line on Edge.Cuts (multi-line)
    # Use a function to find and remove nested s-expressions with Edge.Cuts
    def remove_edge_sexp(t, tag):
        result = []
        i = 0
        while i < len(t):
            if t[i:i+len(tag)+1] == f'({tag}':
                # Check if this block contains Edge.Cuts
                depth = 0
                j = i
                while j < len(t):
                    if t[j] == '(':
                        depth += 1
                    elif t[j] == ')':
                        depth -= 1
                        if depth == 0:
                            break
                    elif t[j] == '"':
                        j += 1
                        while j < len(t) and t[j] != '"':
                            if t[j] == '\\':
                                j += 1
                            j += 1
                    j += 1
                block = t[i:j+1]
                if 'Edge.Cuts' in block:
                    # Skip preceding whitespace too
                    while result and result[-1] in '\n\t ':
                        result.pop()
                    i = j + 1
                    continue
            result.append(t[i])
            i += 1
        return ''.join(result)

    for tag in ('gr_line', 'gr_rect', 'gr_arc', 'gr_poly'):
        text = remove_edge_sexp(text, tag)

    return text


# ---------------------------------------------------------------------------
# S-expression generators — all coordinates go through board_to_pcb()
# ---------------------------------------------------------------------------

def generate_board_outline(width, height):
    """
    Generate Edge.Cuts rectangle from BOARD:WxH.
    Anchored at VIEWPORT_OFFSET. Always regenerated.
    """
    uid = str(uuid.uuid4())
    x1, y1 = board_to_pcb(0, 0)
    x2, y2 = board_to_pcb(width, height)
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
        f'\t\t(uuid "{uid}")\n'
        f'\t)'
    )


def generate_track_sexpr(track, net_map):
    """Generate a (segment ...) s-expression, translating to pcb coordinates."""
    net_num = net_map.get(track['net'], 0)
    uid = str(uuid.uuid4())
    x1, y1 = board_to_pcb(track['x1'], track['y1'])
    x2, y2 = board_to_pcb(track['x2'], track['y2'])
    return (
        f'\t(segment\n'
        f'\t\t(start {x1} {y1})\n'
        f'\t\t(end {x2} {y2})\n'
        f'\t\t(width {track["width"]})\n'
        f'\t\t(layer "{track["layer"]}")\n'
        f'\t\t(net {net_num})\n'
        f'\t\t(uuid "{uid}")\n'
        f'\t)'
    )


def generate_via_sexpr(via, net_map):
    """Generate a (via ...) s-expression."""
    net_num = net_map.get(via['net'], 0)
    uid = str(uuid.uuid4())
    size = round(via['drill'] + 2 * via['annular'], 3)
    x, y = board_to_pcb(via['x'], via['y'])
    return (
        f'\t(via\n'
        f'\t\t(at {x} {y})\n'
        f'\t\t(size {size})\n'
        f'\t\t(drill {via["drill"]})\n'
        f'\t\t(layers "F.Cu" "B.Cu")\n'
        f'\t\t(net {net_num})\n'
        f'\t\t(uuid "{uid}")\n'
        f'\t)'
    )


def generate_zone_sexpr(zone, net_map):
    """Generate a (zone ...) s-expression."""
    net_num = net_map.get(zone['net'], 0)
    uid = str(uuid.uuid4())
    pts = "\n".join(
        f'\t\t\t\t(xy {board_to_pcb(p[0], p[1])[0]} {board_to_pcb(p[0], p[1])[1]})'
        for p in zone['points']
    )
    return (
        f'\t(zone\n'
        f'\t\t(net {net_num})\n'
        f'\t\t(net_name "{zone["net"]}")\n'
        f'\t\t(layer "{zone["layer"]}")\n'
        f'\t\t(uuid "{uid}")\n'
        f'\t\t(hatch edge 0.5)\n'
        f'\t\t(connect_pads\n'
        f'\t\t\t(clearance 0.2)\n'
        f'\t\t)\n'
        f'\t\t(min_thickness 0.25)\n'
        f'\t\t(filled_areas_thickness no)\n'
        f'\t\t(fill yes\n'
        f'\t\t\t(thermal_gap 0.5)\n'
        f'\t\t\t(thermal_bridge_width 0.5)\n'
        f'\t\t)\n'
        f'\t\t(polygon\n'
        f'\t\t\t(pts\n'
        f'{pts}\n'
        f'\t\t\t)\n'
        f'\t\t)\n'
        f'\t)'
    )


# ---------------------------------------------------------------------------
# Main patching logic
# ---------------------------------------------------------------------------

def patch_pcb(pcb_text, dpcb):
    """Apply .dpcb changes to .kicad_pcb text."""

    net_map = build_net_map(pcb_text)
    result = pcb_text

    # 1. Update footprint positions (translate board→pcb coordinates)
    fp_blocks = find_footprint_blocks(result)
    updates = []
    for ref, (start, end, block_text) in fp_blocks.items():
        if ref in dpcb['footprints']:
            fp = dpcb['footprints'][ref]
            new_block = update_footprint_position(
                block_text, fp['x'], fp['y'], fp['rotation']
            )
            new_block = update_footprint_layer(new_block, fp['layer'])
            updates.append((start, end, new_block))

    for start, end, new_block in sorted(updates, key=lambda x: x[0], reverse=True):
        result = result[:start] + new_block + result[end:]

    # 2. Remove existing tracks, vias, zones
    result = remove_all_segments(result)
    result = remove_all_vias(result)
    if dpcb['zones']:
        result = remove_all_zones(result)

    # 3. Always remove and regenerate Edge.Cuts
    result = remove_edge_cuts(result)

    # 4. Find insertion point — before the final closing paren
    insert_pos = result.rstrip().rfind(')')
    embedded_match = result.rfind('(embedded_fonts')
    if embedded_match != -1 and embedded_match > insert_pos - 50:
        insert_pos = embedded_match
        while insert_pos > 0 and result[insert_pos - 1] in '\n\t ':
            insert_pos -= 1

    new_content = []

    # 5. Board outline — ALWAYS generated from BOARD:WxH
    if dpcb['board']:
        w, h = dpcb['board']
        new_content.append(generate_board_outline(w, h))
        print(f"  Board outline: {w}x{h}mm at viewport offset "
              f"({VIEWPORT_X},{VIEWPORT_Y})", file=sys.stderr)
    else:
        print("  WARNING: No BOARD: line in .dpcb — no Edge.Cuts generated",
              file=sys.stderr)

    # 6. Tracks
    for track in dpcb['tracks']:
        new_content.append(generate_track_sexpr(track, net_map))

    # 7. Vias
    for via in dpcb['vias']:
        new_content.append(generate_via_sexpr(via, net_map))

    # 8. Zones
    for zone in dpcb['zones']:
        new_content.append(generate_zone_sexpr(zone, net_map))

    if new_content:
        insertion = '\n' + '\n'.join(new_content) + '\n'
        result = result[:insert_pos] + insertion + result[insert_pos:]

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Patch a .kicad_pcb with layout changes from a .dpcb file"
    )
    parser.add_argument("base_pcb", help="Input .kicad_pcb (provides footprint geometry)")
    parser.add_argument("dpcb",     help="Input .dpcb (provides layout intent)")
    parser.add_argument("-o", "--output", required=True, help="Output .kicad_pcb")
    parser.add_argument("--viewport-x", type=float, default=120.0,
                        help="Viewport X offset in mm (default 120.0)")
    parser.add_argument("--viewport-y", type=float, default=65.0,
                        help="Viewport Y offset in mm (default 65.0)")
    args = parser.parse_args()

    # Allow overriding viewport offset from command line
    global VIEWPORT_X, VIEWPORT_Y
    VIEWPORT_X = args.viewport_x
    VIEWPORT_Y = args.viewport_y

    base_path   = Path(args.base_pcb)
    dpcb_path   = Path(args.dpcb)
    output_path = Path(args.output)

    if not base_path.exists():
        print(f"Error: {base_path} not found", file=sys.stderr)
        sys.exit(1)
    if not dpcb_path.exists():
        print(f"Error: {dpcb_path} not found", file=sys.stderr)
        sys.exit(1)

    pcb_text  = base_path.read_text(encoding='utf-8')
    dpcb_text = dpcb_path.read_text(encoding='utf-8')
    dpcb      = parse_dpcb(dpcb_text)
    print(len(dpcb['footprints']), len(dpcb['tracks']))

    result = patch_pcb(pcb_text, dpcb)
    output_path.write_text(result, encoding='utf-8')

    n_fp   = len(dpcb['footprints'])
    n_trk  = len(dpcb['tracks'])
    n_via  = len(dpcb['vias'])
    n_zone = len(dpcb['zones'])
    print(f"Patched: {base_path} + {dpcb_path} -> {output_path}", file=sys.stderr)
    print(f"  {n_fp} footprints repositioned", file=sys.stderr)
    print(f"  {n_trk} tracks, {n_via} vias, {n_zone} zones", file=sys.stderr)


if __name__ == "__main__":
    main()