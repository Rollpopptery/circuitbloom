#!/usr/bin/env python3
"""
gen_pin_atlas.py - Generate a compact pin position atlas from KiCad symbol libraries.

Scans KiCad symbol library files (.kicad_sym) and/or schematic files (.kicad_sch)
to extract pin connection-point offsets relative to the symbol origin.

Output format is minimal: { "lib:symbol": { "pin_num": [x, y], ... }, ... }
This is the only data needed for AI-driven schematic generation - knowing where
pins land in schematic space allows correct wire placement.

For pin names, types, and other metadata, refer to the .kicad_sym files directly.

Usage:
    python gen_pin_atlas.py                                  # Auto-detect KiCad libs
    python gen_pin_atlas.py /usr/share/kicad/symbols/        # Scan directory
    python gen_pin_atlas.py Device.kicad_sym                  # Single library
    python gen_pin_atlas.py my_project.kicad_sch              # From schematic
    python gen_pin_atlas.py -o atlas.json /path/to/symbols/   # Custom output
"""

import sys
import os
import json
import glob
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
        self.pos += 1
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
        self.pos += 1
        start = self.pos
        while self.pos < self.length and self.text[self.pos] != '"':
            if self.text[self.pos] == '\\':
                self.pos += 1
            self.pos += 1
        val = self.text[start:self.pos]
        if self.pos < self.length:
            self.pos += 1
        return val

    def parse_atom(self):
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in ' \t\n\r()':
            self.pos += 1
        return self.text[start:self.pos]


def find_nodes(tree, tag):
    if not isinstance(tree, list):
        return []
    return [c for c in tree if isinstance(c, list) and len(c) > 0 and c[0] == tag]


def find_node(tree, tag):
    nodes = find_nodes(tree, tag)
    return nodes[0] if nodes else None


def to_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def extract_pin_offsets(sym_node):
    """Recursively extract pin_number -> [x, y] from a symbol definition."""
    pins = {}
    _collect_pins(sym_node, pins)
    return pins


def _collect_pins(node, pins):
    if not isinstance(node, list):
        return
    if len(node) > 0 and node[0] == 'pin':
        at_node = find_node(node, 'at')
        num_node = find_node(node, 'number')
        if at_node and len(at_node) >= 3 and num_node and len(num_node) >= 2:
            x = round(to_float(at_node[1]), 2)
            y = round(to_float(at_node[2]), 2)
            pins[num_node[1]] = [x, y]
    else:
        for child in node:
            if isinstance(child, list):
                _collect_pins(child, pins)


def process_lib(tree, lib_name=None):
    """Process parsed tree, return {symbol_id: {pin: [x,y]}}."""
    atlas = {}
    for sym in find_nodes(tree, 'symbol'):
        if len(sym) < 2:
            continue
        sym_name = sym[1]
        if lib_name and ':' not in sym_name:
            full_id = f"{lib_name}:{sym_name}"
        else:
            full_id = sym_name

        pins = extract_pin_offsets(sym)
        if pins:
            atlas[full_id] = pins
    return atlas


def parse_sym_file(filepath):
    """Parse a .kicad_sym file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    parser = SExpParser(content)
    tree = parser.parse()
    if not tree or tree[0] != 'kicad_symbol_lib':
        return {}
    lib_name = os.path.splitext(os.path.basename(filepath))[0]
    return process_lib(tree, lib_name)


def parse_sch_file(filepath):
    """Extract pin offsets from a .kicad_sch lib_symbols section."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    parser = SExpParser(content)
    tree = parser.parse()
    if not tree or tree[0] != 'kicad_sch':
        return {}
    lib_syms = find_node(tree, 'lib_symbols')
    if not lib_syms:
        return {}
    return process_lib(lib_syms)


def find_default_lib_paths():
    """Find KiCad symbol library paths on the system."""
    candidates = [
        '/usr/share/kicad/symbols',
        '/usr/local/share/kicad/symbols',
        os.path.expanduser('~/.local/share/kicad/symbols'),
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols',
        'C:/Program Files/KiCad/share/kicad/symbols',
        'C:/Program Files/KiCad/9.0/share/kicad/symbols',
    ]
    for ver in ['8', '9', '10']:
        env_dir = os.environ.get(f'KICAD{ver}_SYMBOL_DIR')
        if env_dir:
            candidates.insert(0, env_dir)
    env_dir = os.environ.get('KICAD_SYMBOL_DIR')
    if env_dir:
        candidates.insert(0, env_dir)
    return [p for p in candidates if os.path.isdir(p)]


def generate_atlas(sources, output_path='kicad_pin_atlas.json'):
    """Main generation function."""
    atlas = {}
    lib_paths_used = []
    file_count = 0

    for source in sources:
        if os.path.isdir(source):
            sym_files = sorted(glob.glob(os.path.join(source, '*.kicad_sym')))
            if not sym_files:
                print(f"  Warning: No .kicad_sym files in {source}")
                continue
            lib_paths_used.append(source)
            print(f"  Scanning: {source} ({len(sym_files)} files)")
            for sf in sym_files:
                lib_name = os.path.splitext(os.path.basename(sf))[0]
                try:
                    entries = parse_sym_file(sf)
                    atlas.update(entries)
                    file_count += 1
                    if entries:
                        print(f"    {lib_name}: {len(entries)} symbols")
                except Exception as e:
                    print(f"    {lib_name}: ERROR - {e}")

        elif source.endswith('.kicad_sym'):
            lib_paths_used.append(source)
            try:
                entries = parse_sym_file(source)
                atlas.update(entries)
                file_count += 1
                print(f"  {os.path.basename(source)}: {len(entries)} symbols")
            except Exception as e:
                print(f"  {os.path.basename(source)}: ERROR - {e}")

        elif source.endswith('.kicad_sch'):
            lib_paths_used.append(source)
            try:
                entries = parse_sch_file(source)
                atlas.update(entries)
                file_count += 1
                print(f"  {os.path.basename(source)} (embedded): {len(entries)} symbols")
            except Exception as e:
                print(f"  {os.path.basename(source)}: ERROR - {e}")
        else:
            print(f"  Skipping: {source}")

    if not atlas:
        print("\nNo symbols found.")
        return None

    # Build output: metadata + sorted compact entries
    output = {
        '_meta': {
            'generated_by': 'gen_pin_atlas.py',
            'format': 'compact: {lib:sym: {pin_num: [x_offset, y_offset]}}',
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'sources': lib_paths_used,
            'files_processed': file_count,
            'symbol_count': len(atlas),
        },
    }
    for key in sorted(atlas.keys()):
        output[key] = atlas[key]

    # Use separators to minimize whitespace
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, separators=(',', ':'), ensure_ascii=False)

    size = os.path.getsize(output_path)
    print(f"\nAtlas: {output_path}")
    print(f"  Symbols: {len(atlas)}")
    print(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Generate KiCad pin atlas for AI schematic generation.')
    parser.add_argument('sources', nargs='*',
                        help='.kicad_sym files, .kicad_sch files, or directories')
    parser.add_argument('-o', '--output', default='kicad_pin_atlas.json',
                        help='Output path (default: kicad_pin_atlas.json)')
    args = parser.parse_args()

    sources = list(args.sources)
    if not sources:
        print("Searching for KiCad libraries...")
        sources = find_default_lib_paths()
        if not sources:
            print("No KiCad libraries found. Specify paths manually.")
            sys.exit(1)
        print(f"Found: {', '.join(sources)}")

    print(f"\nGenerating pin atlas...")
    if not generate_atlas(sources, args.output):
        sys.exit(1)


if __name__ == '__main__':
    main()