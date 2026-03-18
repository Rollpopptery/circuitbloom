#!/usr/bin/env python3
"""
distill_sch.py - Distill KiCad schematic files into compact, token-efficient representations.

Parses .kicad_sch S-expression files and extracts only the electrically meaningful
information: component instances, pin mappings, wire connections, labels, power symbols,
hierarchical sheets, and net topology.

Strips all graphical boilerplate (polylines, arcs, circles, fonts, stroke styles,
fill types, UUIDs, coordinates of decorative elements) that are irrelevant to
circuit understanding and design intent.

Usage:
    python distill_sch.py input.kicad_sch [output.dsch]
    
If no output path given, writes to input_distilled.dsch
"""

import sys
import re
import os
from datetime import datetime


class SExpParser:
    """Minimal S-expression parser for KiCad files."""
    
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.length = len(text)
    
    def skip_whitespace(self):
        while self.pos < self.length and self.text[self.pos] in ' \t\n\r':
            self.pos += 1
    
    def parse(self):
        self.skip_whitespace()
        if self.pos >= self.length:
            return None
        ch = self.text[self.pos]
        if ch == '(':
            return self.parse_list()
        elif ch == '"':
            return self.parse_string()
        else:
            return self.parse_atom()
    
    def parse_list(self):
        self.pos += 1  # skip '('
        result = []
        while True:
            self.skip_whitespace()
            if self.pos >= self.length:
                break
            if self.text[self.pos] == ')':
                self.pos += 1
                break
            result.append(self.parse())
        return result
    
    def parse_string(self):
        self.pos += 1  # skip opening "
        start = self.pos
        while self.pos < self.length and self.text[self.pos] != '"':
            if self.text[self.pos] == '\\':
                self.pos += 1  # skip escaped char
            self.pos += 1
        val = self.text[start:self.pos]
        if self.pos < self.length:
            self.pos += 1  # skip closing "
        return val
    
    def parse_atom(self):
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in ' \t\n\r()':
            self.pos += 1
        return self.text[start:self.pos]


def find_nodes(tree, tag):
    """Find all child nodes with given tag (first element)."""
    if not isinstance(tree, list):
        return []
    return [child for child in tree if isinstance(child, list) and len(child) > 0 and child[0] == tag]


def find_node(tree, tag):
    """Find first child node with given tag."""
    nodes = find_nodes(tree, tag)
    return nodes[0] if nodes else None


def get_prop(tree, prop_name):
    """Get a property value from a symbol or other node."""
    for child in tree:
        if isinstance(child, list) and len(child) >= 3 and child[0] == 'property' and child[1] == prop_name:
            return child[2]
    return None


def extract_at(node):
    """Extract (x, y, rotation) from an 'at' node."""
    at = find_node(node, 'at')
    if at and len(at) >= 3:
        x, y = at[1], at[2]
        rot = at[3] if len(at) > 3 else '0'
        return (x, y, rot)
    return None


def extract_pins_from_libsym(sym):
    """Extract pin number -> pin name mapping from a lib_symbol definition."""
    pins = {}
    _collect_pins_recursive(sym, pins)
    return pins


def _collect_pins_recursive(node, pins):
    """Recursively find all pin definitions in a symbol tree."""
    if not isinstance(node, list):
        return
    if len(node) > 0 and node[0] == 'pin':
        # pin format: (pin <type> <style> (at ...) (length ...) (name <n> ...) (number <n> ...))
        name_node = find_node(node, 'name')
        num_node = find_node(node, 'number')
        if num_node and len(num_node) >= 2:
            pin_num = num_node[1]
            pin_name = name_node[1] if name_node and len(name_node) >= 2 else '~'
            # Get pin type (first atom after 'pin')
            pin_type = node[1] if len(node) > 1 and isinstance(node[1], str) else ''
            if pin_name == '~':
                pins[pin_num] = f"~:{pin_type}" if pin_type else "~"
            else:
                pins[pin_num] = f"{pin_name}:{pin_type}" if pin_type else pin_name
    else:
        for child in node:
            if isinstance(child, list):
                _collect_pins_recursive(child, pins)


def distill_lib_symbols(tree):
    """Extract compact pin maps from lib_symbols section."""
    lib_syms = find_node(tree, 'lib_symbols')
    if not lib_syms:
        return {}
    
    result = {}
    for sym in find_nodes(lib_syms, 'symbol'):
        if len(sym) < 2:
            continue
        sym_name = sym[1]
        
        # Get key properties
        is_power = False
        for child in sym:
            if isinstance(child, list) and len(child) > 0 and child[0] == 'power':
                is_power = True
        
        # Get footprint filters
        fp_filters = get_prop(sym, 'ki_fp_filters')
        
        pins = extract_pins_from_libsym(sym)
        
        result[sym_name] = {
            'pins': pins,
            'power': is_power,
            'fp_filters': fp_filters,
        }
    
    return result


def distill_symbol_instances(tree):
    """Extract placed component instances."""
    instances = []
    for sym in find_nodes(tree, 'symbol'):
        lib_id_node = find_node(sym, 'lib_id')
        if not lib_id_node or len(lib_id_node) < 2:
            continue
        
        lib_id = lib_id_node[1]
        at = extract_at(sym)
        
        # Unit
        unit_node = find_node(sym, 'unit')
        unit = unit_node[1] if unit_node and len(unit_node) >= 2 else '1'
        
        # Mirror
        mirror_node = find_node(sym, 'mirror')
        mirror = mirror_node[1] if mirror_node and len(mirror_node) >= 2 else None
        
        # Properties
        ref = get_prop(sym, 'Reference') or '?'
        value = get_prop(sym, 'Value') or ''
        footprint = get_prop(sym, 'Footprint') or ''
        
        # Pin instance UUIDs (for net tracking)
        pin_uuids = {}
        for pin in find_nodes(sym, 'pin'):
            if len(pin) >= 2:
                pin_num = pin[1]
                uuid_node = find_node(pin, 'uuid')
                if uuid_node and len(uuid_node) >= 2:
                    pin_uuids[pin_num] = uuid_node[1]
        
        # DNP flag
        dnp_node = find_node(sym, 'dnp')
        dnp = dnp_node[1] if dnp_node and len(dnp_node) >= 2 else 'no'
        
        # Exclude from sim
        excl_node = find_node(sym, 'exclude_from_sim')
        exclude_sim = excl_node[1] if excl_node and len(excl_node) >= 2 else 'no'
        
        inst = {
            'lib_id': lib_id,
            'ref': ref,
            'value': value,
            'footprint': footprint,
            'at': at,
            'unit': unit,
            'mirror': mirror,
            'dnp': dnp,
            'exclude_sim': exclude_sim,
        }
        instances.append(inst)
    
    return instances


def distill_wires(tree):
    """Extract wire connections as endpoint pairs."""
    wires = []
    for wire in find_nodes(tree, 'wire'):
        pts = find_node(wire, 'pts')
        if pts:
            points = find_nodes(pts, 'xy')
            if len(points) >= 2:
                p1 = (points[0][1], points[0][2])
                p2 = (points[1][1], points[1][2])
                wires.append((p1, p2))
    return wires


def distill_labels(tree):
    """Extract net labels."""
    labels = []
    for tag in ['label', 'global_label', 'hierarchical_label']:
        for lbl in find_nodes(tree, tag):
            if len(lbl) >= 2:
                name = lbl[1]
                at = extract_at(lbl)
                labels.append({
                    'type': tag,
                    'name': name,
                    'at': at,
                })
    return labels


def distill_junctions(tree):
    """Extract junction points."""
    junctions = []
    for jnc in find_nodes(tree, 'junction'):
        at = extract_at(jnc)
        if at:
            junctions.append((at[0], at[1]))
    return junctions


def distill_no_connects(tree):
    """Extract no-connect markers."""
    ncs = []
    for nc in find_nodes(tree, 'no_connect'):
        at = extract_at(nc)
        if at:
            ncs.append((at[0], at[1]))
    return ncs


def distill_power_flags(tree):
    """Extract power flag locations."""
    flags = []
    for sym in find_nodes(tree, 'symbol'):
        lib_id_node = find_node(sym, 'lib_id')
        if lib_id_node and 'PWR_FLAG' in lib_id_node[1]:
            at = extract_at(sym)
            if at:
                flags.append(at)
    return flags


def distill_sheets(tree):
    """Extract hierarchical sheet references."""
    sheets = []
    for sheet in find_nodes(tree, 'sheet'):
        at = extract_at(sheet)
        
        # Size
        size_node = find_node(sheet, 'size')
        size = (size_node[1], size_node[2]) if size_node and len(size_node) >= 3 else None
        
        # Properties
        sheetname = None
        sheetfile = None
        for prop in find_nodes(sheet, 'property'):
            if len(prop) >= 3:
                if prop[1] == 'Sheetname':
                    sheetname = prop[2]
                elif prop[1] == 'Sheetfile':
                    sheetfile = prop[2]
        
        # Sheet pins
        pins = []
        for pin in find_nodes(sheet, 'pin'):
            if len(pin) >= 3:
                pin_name = pin[1]
                pin_dir = pin[2]
                pin_at = extract_at(pin)
                pins.append({
                    'name': pin_name,
                    'direction': pin_dir,
                    'at': pin_at,
                })
        
        sheets.append({
            'name': sheetname,
            'file': sheetfile,
            'at': at,
            'size': size,
            'pins': pins,
        })
    
    return sheets


def distill_text_annotations(tree):
    """Extract text annotations (design notes)."""
    texts = []
    for txt in find_nodes(tree, 'text'):
        if len(txt) >= 2 and isinstance(txt[1], str):
            content = txt[1]
            at = extract_at(txt)
            texts.append({'text': content, 'at': at})
    return texts


def format_at(at):
    """Format position tuple compactly."""
    if not at:
        return ''
    x, y, rot = at
    if rot and rot != '0':
        return f"@({x},{y}):r{rot}"
    return f"@({x},{y})"


def format_output(header, lib_syms, instances, wires, labels, junctions, 
                   no_connects, sheets, texts, source_file):
    """Format everything into the compact .dsch output."""
    lines = []
    
    # Header
    lines.append(f"# DISTILLED SCHEMATIC")
    lines.append(f"# Source: {source_file}")
    lines.append(f"# Distilled by: distill_sch.py")
    lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# NOTE: This is a compressed representation. The full .kicad_sch")
    lines.append(f"#       file is required for KiCad. This format is for AI reasoning.")
    lines.append(f"#")
    
    # File header info
    if header:
        lines.append(f"HDR:v{header.get('version','?')}:gen={header.get('generator','?')}:{header.get('generator_version','?')}:paper={header.get('paper','?')}")
        tb = header.get('title_block', {})
        if tb:
            parts = []
            for k, v in tb.items():
                if v:
                    parts.append(f'{k}="{v}"')
            if parts:
                lines.append(f"TITLE:{':'.join(parts)}")
    lines.append("")
    
    # Library symbol definitions (pin maps only)
    lines.append(f"# === SYMBOL DEFINITIONS ({len(lib_syms)}) ===")
    for sym_name, data in sorted(lib_syms.items()):
        pins = data['pins']
        flags = []
        if data['power']:
            flags.append('power')
        if data['fp_filters']:
            flags.append(f'fp={data["fp_filters"]}')
        
        flag_str = f":{','.join(flags)}" if flags else ""
        
        if pins:
            pin_parts = []
            for num in sorted(pins.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                name = pins[num]
                # Simplify: if name is just "~:passive" or similar, shorten
                if name.startswith('~:'):
                    pin_parts.append(f"{num}")
                else:
                    # Strip type suffix for readability if it's common
                    clean = name.split(':')[0] if ':' in name else name
                    pin_parts.append(f"{num}={clean}")
            lines.append(f"SYM:{sym_name}:pins[{','.join(pin_parts)}]{flag_str}")
        else:
            lines.append(f"SYM:{sym_name}:pins[]{flag_str}")
    lines.append("")
    
    # Component instances - separate power symbols from real components
    power_instances = []
    real_instances = []
    for inst in instances:
        lib = inst['lib_id']
        # Check if it's a power symbol
        sym_short = lib.split(':')[-1] if ':' in lib else lib
        sym_data = lib_syms.get(lib, {})
        if sym_data.get('power', False) or inst['ref'].startswith('#'):
            power_instances.append(inst)
        else:
            real_instances.append(inst)
    
    lines.append(f"# === COMPONENTS ({len(real_instances)}) ===")
    for inst in sorted(real_instances, key=lambda i: i['ref']):
        lib_short = inst['lib_id'].split(':')[-1] if ':' in inst['lib_id'] else inst['lib_id']
        pos = format_at(inst['at'])
        
        parts = [f"COMP:{inst['ref']}:{lib_short}"]
        if inst['value'] and inst['value'] != lib_short:
            parts.append(f"val={inst['value']}")
        parts.append(pos)
        if inst['unit'] != '1':
            parts.append(f"u{inst['unit']}")
        if inst['mirror']:
            parts.append(f"mir={inst['mirror']}")
        if inst['footprint']:
            # Shorten footprint - just package name
            fp = inst['footprint']
            fp_short = fp.split(':')[-1] if ':' in fp else fp
            parts.append(f"fp={fp_short}")
        if inst['dnp'] == 'yes':
            parts.append("DNP")
        if inst['exclude_sim'] == 'yes':
            parts.append("NO_SIM")
        
        lines.append(':'.join(parts))
    lines.append("")
    
    # Power symbols (compact)
    lines.append(f"# === POWER SYMBOLS ({len(power_instances)}) ===")
    # Group by type for compactness
    power_groups = {}
    for inst in power_instances:
        lib_short = inst['lib_id'].split(':')[-1] if ':' in inst['lib_id'] else inst['lib_id']
        val = inst['value'] or lib_short
        key = val
        if key not in power_groups:
            power_groups[key] = []
        at = inst['at']
        if at:
            power_groups[key].append(f"({at[0]},{at[1]})")
    
    for ptype, locations in sorted(power_groups.items()):
        lines.append(f"PWR:{ptype}:{' '.join(locations)}")
    lines.append("")
    
    # Labels
    if labels:
        lines.append(f"# === LABELS ({len(labels)}) ===")
        for lbl in sorted(labels, key=lambda l: l['name']):
            prefix = {'label': 'LBL', 'global_label': 'GLBL', 'hierarchical_label': 'HLBL'}
            tag = prefix.get(lbl['type'], 'LBL')
            pos = format_at(lbl['at'])
            lines.append(f"{tag}:\"{lbl['name']}\"{pos}")
        lines.append("")
    
    # Wires
    if wires:
        lines.append(f"# === WIRES ({len(wires)}) ===")
        for p1, p2 in wires:
            lines.append(f"W:({p1[0]},{p1[1]})->({p2[0]},{p2[1]})")
        lines.append("")
    
    # Junctions
    if junctions:
        lines.append(f"# === JUNCTIONS ({len(junctions)}) ===")
        jpts = [f"({j[0]},{j[1]})" for j in junctions]
        # Pack multiple per line
        chunk_size = 8
        for i in range(0, len(jpts), chunk_size):
            lines.append(f"JNC:{' '.join(jpts[i:i+chunk_size])}")
        lines.append("")
    
    # No connects
    if no_connects:
        lines.append(f"# === NO CONNECTS ({len(no_connects)}) ===")
        ncpts = [f"({nc[0]},{nc[1]})" for nc in no_connects]
        lines.append(f"NC:{' '.join(ncpts)}")
        lines.append("")
    
    # Hierarchical sheets
    if sheets:
        lines.append(f"# === SHEETS ({len(sheets)}) ===")
        for sheet in sheets:
            pos = format_at(sheet['at'])
            lines.append(f"SHEET:\"{sheet['name']}\":\"{sheet['file']}\"{pos}")
            for pin in sheet['pins']:
                ppos = format_at(pin['at'])
                lines.append(f"  PIN:\"{pin['name']}\":{pin['direction']}{ppos}")
        lines.append("")
    
    # Text annotations
    if texts:
        lines.append(f"# === ANNOTATIONS ({len(texts)}) ===")
        for txt in texts:
            pos = format_at(txt['at'])
            # Escape newlines
            content = txt['text'].replace('\n', '\\n')
            lines.append(f"TXT:\"{content}\"{pos}")
        lines.append("")
    
    # Stats
    lines.append(f"# === STATS ===")
    lines.append(f"# Components: {len(real_instances)}")
    lines.append(f"# Power symbols: {len(power_instances)}")
    lines.append(f"# Wires: {len(wires)}")
    lines.append(f"# Labels: {len(labels)}")
    lines.append(f"# Junctions: {len(junctions)}")
    lines.append(f"# No-connects: {len(no_connects)}")
    lines.append(f"# Sheets: {len(sheets)}")
    lines.append(f"# Symbol defs: {len(lib_syms)}")
    
    return '\n'.join(lines)


def extract_header(tree):
    """Extract header information from parsed tree."""
    header = {}
    
    version = find_node(tree, 'version')
    if version and len(version) >= 2:
        header['version'] = version[1]
    
    gen = find_node(tree, 'generator')
    if gen and len(gen) >= 2:
        header['generator'] = gen[1]
    
    gen_ver = find_node(tree, 'generator_version')
    if gen_ver and len(gen_ver) >= 2:
        header['generator_version'] = gen_ver[1]
    
    paper = find_node(tree, 'paper')
    if paper and len(paper) >= 2:
        header['paper'] = paper[1]
    
    tb = find_node(tree, 'title_block')
    if tb:
        title_info = {}
        for tag in ['title', 'date', 'rev', 'company']:
            node = find_node(tb, tag)
            if node and len(node) >= 2:
                title_info[tag] = node[1]
        header['title_block'] = title_info
    
    return header


def distill(input_path, output_path=None):
    """Main distillation function."""
    
    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    if not output_path:
        base = os.path.splitext(input_path)[0]
        output_path = base + '_distilled.dsch'
    
    # Read and parse
    print(f"Reading: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_size = len(content)
    print(f"Original size: {original_size:,} bytes")
    
    parser = SExpParser(content)
    tree = parser.parse()
    
    if not tree or tree[0] != 'kicad_sch':
        print("Error: Not a valid .kicad_sch file", file=sys.stderr)
        sys.exit(1)
    
    # Extract all sections
    print("Extracting header...")
    header = extract_header(tree)
    
    print("Extracting symbol definitions...")
    lib_syms = distill_lib_symbols(tree)
    
    print("Extracting component instances...")
    instances = distill_symbol_instances(tree)
    
    print("Extracting wires...")
    wires = distill_wires(tree)
    
    print("Extracting labels...")
    labels = distill_labels(tree)
    
    print("Extracting junctions...")
    junctions = distill_junctions(tree)
    
    print("Extracting no-connects...")
    no_connects = distill_no_connects(tree)
    
    print("Extracting sheets...")
    sheets = distill_sheets(tree)
    
    print("Extracting annotations...")
    texts = distill_text_annotations(tree)
    
    # Format output
    source_name = os.path.basename(input_path)
    output = format_output(header, lib_syms, instances, wires, labels, 
                           junctions, no_connects, sheets, texts, source_name)
    
    # Write
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output)
    
    distilled_size = len(output)
    ratio = (1 - distilled_size / original_size) * 100
    
    print(f"\nDistilled size: {distilled_size:,} bytes")
    print(f"Compression: {ratio:.1f}% reduction")
    print(f"Written to: {output_path}")
    
    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python distill_sch.py <input.kicad_sch> [output.dsch]")
        print("       Distills KiCad schematic into compact AI-readable format.")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    distill(input_file, output_file)