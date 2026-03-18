#!/usr/bin/env python3
"""
gen_sch.py - Generate valid KiCad schematic files from distilled (.dsch) format.

Reads a distilled schematic, the pin atlas, and the installed KiCad symbol
libraries, then emits a complete .kicad_sch file that KiCad can open directly.

The lib_symbols section is populated by extracting the relevant symbol
definitions from the .kicad_sym library files on disk.

Usage:
    python gen_sch.py input.dsch                              # Auto-detect libs
    python gen_sch.py input.dsch -a kicad_pin_atlas.json      # Specify atlas
    python gen_sch.py input.dsch -l /usr/share/kicad/symbols/ # Specify lib path
    python gen_sch.py input.dsch -m component_mapping.json    # Footprints from mapping
    python gen_sch.py input.dsch -o output.kicad_sch          # Specify output
"""

import sys
import os
import re
import json
import uuid as uuid_mod
from datetime import datetime


def gen_uuid():
    """Generate a KiCad-style UUID."""
    return str(uuid_mod.uuid4())


def parse_at(at_str):
    """Parse @(x,y) or @(x,y):r90 into (x, y, rotation)."""
    m = re.match(r'@\(([^,]+),([^)]+)\)', at_str)
    if not m:
        return (0, 0, 0)
    x, y = float(m.group(1)), float(m.group(2))
    rot = 0
    rm = re.search(r':r(\d+)', at_str)
    if rm:
        rot = int(rm.group(1))
    return (x, y, rot)


def parse_coords(s):
    """Parse (x,y) into (x, y)."""
    m = re.match(r'\(([^,]+),([^)]+)\)', s.strip())
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return None


# ─── Symbol Library Extraction ────────────────────────────────────────────────

def extract_symbol_block_raw(file_content, symbol_name):
    """
    Extract a raw symbol S-expression block from a .kicad_sym file by
    matching parentheses. Returns the exact text including the outer parens.
    
    symbol_name should be the short name (e.g. "R", "GND") as it appears
    in the library file.
    """
    # Find the symbol definition - it's at the top level inside the lib
    # Pattern: (symbol "NAME"\n or (symbol "NAME" 
    patterns = [
        f'(symbol "{symbol_name}"\n',
        f'(symbol "{symbol_name}" ',
        f'(symbol "{symbol_name}"(',
    ]
    
    start = -1
    for pat in patterns:
        start = file_content.find(pat)
        if start >= 0:
            break
    
    if start < 0:
        return None
    
    # Count parens to find the matching close
    depth = 0
    i = start
    while i < len(file_content):
        if file_content[i] == '(':
            depth += 1
        elif file_content[i] == ')':
            depth -= 1
            if depth == 0:
                return file_content[start:i + 1]
        i += 1
    
    return None


def load_symbol_from_lib(lib_dir, lib_id):
    """
    Load a symbol definition from KiCad library files.
    lib_id is like "Device:R" or "power:GND".
    Returns the raw S-expression text with the name changed to include
    the library prefix (as KiCad expects in .kicad_sch files).
    """
    if ':' not in lib_id:
        return None
    
    lib_name, sym_name = lib_id.split(':', 1)
    lib_file = os.path.join(lib_dir, f'{lib_name}.kicad_sym')
    
    if not os.path.exists(lib_file):
        return None
    
    with open(lib_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    block = extract_symbol_block_raw(content, sym_name)
    if not block:
        return None
    
    # Rename ONLY the top-level symbol to include library prefix
    # (symbol "R" -> (symbol "Device:R"
    # Sub-symbols like "R_0_1", "R_1_1" must keep their short names
    block = block.replace(f'(symbol "{sym_name}"', f'(symbol "{lib_id}"', 1)
    
    return block


def find_lib_dir():
    """Find the KiCad symbol library directory."""
    candidates = [
        '/usr/share/kicad/symbols',
        '/usr/local/share/kicad/symbols',
        os.path.expanduser('~/.local/share/kicad/symbols'),
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols',
        'C:/Program Files/KiCad/share/kicad/symbols',
    ]
    for ver in ['8', '9', '10']:
        env_dir = os.environ.get(f'KICAD{ver}_SYMBOL_DIR')
        if env_dir:
            candidates.insert(0, env_dir)
    env_dir = os.environ.get('KICAD_SYMBOL_DIR')
    if env_dir:
        candidates.insert(0, env_dir)
    
    for p in candidates:
        if os.path.isdir(p):
            return p
    return None


# ─── Component Mapping ────────────────────────────────────────────────────────

def load_component_mapping(mapping_path):
    """Load component_mapping.json (new format with lib_id + footprint).
    
    Supports both old format (flat strings) and new format (objects):
        Old: {"74HC4094": "4xxx:HEF4094B"}
        New: {"74HC4094": {"lib_id": "4xxx:HEF4094B", "footprint": "Package_SO:..."}}
    
    Returns a dict of lib_id -> footprint for footprint lookup.
    """
    if not mapping_path or not os.path.exists(mapping_path):
        return {}
    
    with open(mapping_path, 'r') as f:
        raw = json.load(f)
    
    fp_map = {}  # lib_id -> footprint
    for key, val in raw.items():
        if isinstance(val, dict):
            lib_id = val.get('lib_id', '')
            footprint = val.get('footprint', '')
            if lib_id and footprint:
                fp_map[lib_id] = footprint
        # Old format (string) has no footprint info, skip
    
    return fp_map


# ─── .dsch Parser ─────────────────────────────────────────────────────────────

class DschParser:
    """Parse a .dsch distilled schematic file."""

    def __init__(self, text):
        self.header = {}
        self.title = {}
        self.sym_defs = []
        self.components = []
        self.power_symbols = []
        self.labels = []
        self.wires = []
        self.junctions = []
        self.no_connects = []
        self.texts = []
        self.sheets = []
        self._parse(text)

    def _parse(self, text):
        for raw_line in text.split('\n'):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('HDR:'):
                self._parse_header(line)
            elif line.startswith('TITLE:'):
                self._parse_title(line)
            elif line.startswith('SYM:'):
                self._parse_sym(line)
            elif line.startswith('COMP:'):
                self._parse_comp(line)
            elif line.startswith('PWR:'):
                self._parse_power(line)
            elif line.startswith('LBL:') or line.startswith('GLBL:') or line.startswith('HLBL:'):
                self._parse_label(line)
            elif line.startswith('W:'):
                self._parse_wire(line)
            elif line.startswith('JNC:'):
                self._parse_junction(line)
            elif line.startswith('NC:'):
                self._parse_no_connect(line)
            elif line.startswith('TXT:'):
                self._parse_text(line)
            elif line.startswith('SHEET:'):
                self._parse_sheet(line)

    def _parse_header(self, line):
        for part in line[4:].split(':'):
            if part.startswith('v'):
                self.header['version'] = part[1:]
            elif part.startswith('gen='):
                self.header['generator'] = part[4:]
            elif part.startswith('paper='):
                self.header['paper'] = part[6:]
            elif re.match(r'^\d+\.\d+', part):
                self.header['generator_version'] = part

    def _parse_title(self, line):
        for m in re.finditer(r'(\w+)="([^"]*)"', line):
            self.title[m.group(1)] = m.group(2)

    def _parse_sym(self, line):
        self.sym_defs.append(line)


    def _parse_comp(self, line):
        parts = line[5:].split(':')
        comp = {'ref': parts[0]}
        
        # Symbol may be "lib:name" which splits into two parts
        # Check if parts[2] looks like a symbol name (not a key=value)
        if len(parts) > 2 and '=' not in parts[2] and '@' not in parts[2]:
            comp['symbol'] = parts[1] + ':' + parts[2]
            rest = ':'.join(parts[3:])
        else:
            comp['symbol'] = parts[1]
            rest = ':'.join(parts[2:])    

        m = re.search(r'val=([^:@]+)', rest)
        comp['value'] = m.group(1) if m else comp['symbol']

        m = re.search(r'@\([^)]+\)(?::r\d+)?', rest)
        comp['at'] = parse_at(m.group(0)) if m else (100, 100, 0)

        m = re.search(r'fp=([^\s:]+(?::[^\s:]+)*)', rest)
        comp['footprint'] = m.group(1) if m else ''

        m = re.search(r'\bu(\d+)\b', rest)
        comp['unit'] = int(m.group(1)) if m else 1

        m = re.search(r'mir=(\w+)', rest)
        comp['mirror'] = m.group(1) if m else None

        comp['dnp'] = 'DNP' in rest
        comp['no_sim'] = 'NO_SIM' in rest

        self.components.append(comp)

    def _parse_power(self, line):
        parts = line[4:].split(':')
        ptype = parts[0]
        coords_str = ':'.join(parts[1:])
        for m in re.finditer(r'\(([^,]+),([^)]+)\)', coords_str):
            self.power_symbols.append({
                'type': ptype,
                'x': float(m.group(1)),
                'y': float(m.group(2)),
            })

    def _parse_label(self, line):
        ltype = 'label'
        if line.startswith('GLBL:'):
            ltype = 'global_label'
            line = line[5:]
        elif line.startswith('HLBL:'):
            ltype = 'hierarchical_label'
            line = line[5:]
        else:
            line = line[4:]
        m = re.match(r'"([^"]+)"(.*)', line)
        if m:
            name = m.group(1)
            rest = m.group(2)
            at = parse_at(rest) if '@' in rest else (0, 0, 0)
            self.labels.append({'type': ltype, 'name': name, 'at': at})

    def _parse_wire(self, line):
        m = re.match(r'W:\(([^,]+),([^)]+)\)->\(([^,]+),([^)]+)\)', line)
        if m:
            self.wires.append({
                'x1': float(m.group(1)), 'y1': float(m.group(2)),
                'x2': float(m.group(3)), 'y2': float(m.group(4)),
            })

    def _parse_junction(self, line):
        for m in re.finditer(r'\(([^,]+),([^)]+)\)', line):
            self.junctions.append((float(m.group(1)), float(m.group(2))))

    def _parse_no_connect(self, line):
        for m in re.finditer(r'\(([^,]+),([^)]+)\)', line):
            self.no_connects.append((float(m.group(1)), float(m.group(2))))

    def _parse_text(self, line):
        m = re.match(r'TXT:"([^"]*)"(.*)', line)
        if not m:
            return
        text = m.group(1).replace('\\n', '\n')
        rest = m.group(2)
        at = parse_at(rest) if '@' in rest else (0, 0, 0)
        self.texts.append({'text': text, 'at': at})

    def _parse_sheet(self, line):
        m = re.match(r'SHEET:"([^"]+)":"([^"]+)"(.*)', line)
        if m:
            rest = m.group(3)
            at = parse_at(rest) if '@' in rest else (0, 0, 0)
            self.sheets.append({
                'name': m.group(1), 'file': m.group(2), 'at': at,
            })

    def get_all_lib_ids(self):
        """Return all unique lib_ids needed by this schematic."""
        ids = set()

        for comp in self.components:
            ids.add(self._resolve_lib_id(comp['symbol']))

        for pwr in self.power_symbols:
            ids.add(f'power:{pwr["type"]}')

        return ids

    def _resolve_lib_id(self, symbol_short):
        if ':' in symbol_short:
            return symbol_short
        defaults = {
            'R': 'Device:R', 'C': 'Device:C', 'CP': 'Device:CP',
            'L': 'Device:L', 'D': 'Device:D',
            'D_Schottky': 'Device:D_Schottky', 'D_Zener': 'Device:D_Zener',
            'LED': 'Device:LED',
            'Q_NPN_BCE': 'Device:Q_NPN_BCE', 'Q_PNP_BCE': 'Device:Q_PNP_BCE',
        }
        return defaults.get(symbol_short, f'Device:{symbol_short}')


# ─── KiCad Schematic Writer ──────────────────────────────────────────────────

class KicadSchWriter:
    """Generate a .kicad_sch file from parsed distilled schematic data."""

    def __init__(self, dsch, atlas=None, lib_dir=None, fp_map=None):
        self.d = dsch
        self.atlas = atlas or {}
        self.lib_dir = lib_dir or find_lib_dir()
        self.fp_map = fp_map or {}  # lib_id -> footprint
        self.project_uuid = gen_uuid()
        self.pwr_counter = 0

    def generate(self):
        lines = []
        lines.append(self._header())
        lines.append(self._lib_symbols())
        lines.append(self._junctions())
        lines.append(self._no_connects())
        lines.append(self._wires())
        lines.append(self._labels())
        lines.append(self._texts())
        lines.append(self._components())
        lines.append(self._power_symbols())
        lines.append(self._sheets())
        lines.append(self._sheet_instances())
        lines.append(')')
        return '\n'.join(lines)

    def _header(self):
        version = self.d.header.get('version', '20231120')
        generator = self.d.header.get('generator', 'gen_sch')
        gen_ver = self.d.header.get('generator_version', '1.0')
        paper = self.d.header.get('paper', 'A4')

        out = f'''(kicad_sch
\t(version {version})
\t(generator "{generator}")
\t(generator_version "{gen_ver}")
\t(uuid "{self.project_uuid}")
\t(paper "{paper}")'''

        if self.d.title:
            out += '\n\t(title_block'
            for key in ['title', 'date', 'rev', 'company']:
                val = self.d.title.get(key, '')
                if val:
                    out += f'\n\t\t({key} "{val}")'
            out += '\n\t)'

        return out

    def _lib_symbols(self):
        """Extract needed symbol definitions from installed KiCad libraries."""
        lib_ids = self.d.get_all_lib_ids()
        
        out = '\t(lib_symbols'
        
        if not self.lib_dir:
            print("  WARNING: No KiCad library directory found!")
            print("  Symbols will be missing. Specify with -l flag.")
            out += '\n\t)'
            return out
        
        loaded = 0
        missing = []
        
        for lib_id in sorted(lib_ids):
            block = load_symbol_from_lib(self.lib_dir, lib_id)
            if block:
                # Indent the block to fit inside lib_symbols
                indented = self._indent_block(block, 2)
                out += f'\n{indented}'
                loaded += 1
            else:
                missing.append(lib_id)
                print(f"  WARNING: Symbol not found in libraries: {lib_id}")
        
        out += '\n\t)'
        
        print(f"  lib_symbols: {loaded} loaded" + 
              (f", {len(missing)} missing" if missing else ""))
        
        return out

    def _indent_block(self, text, tab_level):
        """Re-indent an S-expression block to the given tab level."""
        lines = text.split('\n')
        result = []
        for i, line in enumerate(lines):
            stripped = line.lstrip('\t')
            if i == 0:
                # First line gets the target indent
                result.append('\t' * tab_level + stripped)
            else:
                # Subsequent lines: count original indent and add offset
                orig_tabs = len(line) - len(stripped)
                # Add tab_level to whatever indent was there
                result.append('\t' * (orig_tabs + tab_level) + stripped)
        return '\n'.join(result)

    def _junctions(self):
        out = ''
        for jx, jy in self.d.junctions:
            out += f'''
\t(junction
\t\t(at {jx} {jy})
\t\t(diameter 0)
\t\t(color 0 0 0 0)
\t\t(uuid "{gen_uuid()}")
\t)'''
        return out

    def _no_connects(self):
        out = ''
        for nx, ny in self.d.no_connects:
            out += f'''
\t(no_connect
\t\t(at {nx} {ny})
\t\t(uuid "{gen_uuid()}")
\t)'''
        return out

    def _wires(self):
        out = ''
        for w in self.d.wires:
            out += f'''
\t(wire
\t\t(pts
\t\t\t(xy {w['x1']} {w['y1']}) (xy {w['x2']} {w['y2']})
\t\t)
\t\t(stroke
\t\t\t(width 0)
\t\t\t(type default)
\t\t)
\t\t(uuid "{gen_uuid()}")
\t)'''
        return out

    def _labels(self):
        out = ''
        for lbl in self.d.labels:
            tag = lbl['type']
            name = lbl['name']
            x, y, rot = lbl['at']
            out += f'''
\t({tag} "{name}"
\t\t(at {x} {y} {rot})
\t\t(fields_autoplaced yes)
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "{gen_uuid()}")
\t)'''
        return out

    def _texts(self):
        out = ''
        for txt in self.d.texts:
            x, y, rot = txt['at']
            content = txt['text']
            out += f'''
\t(text "{content}"
\t\t(exclude_from_sim no)
\t\t(at {x} {y} {rot})
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "{gen_uuid()}")
\t)'''
        return out

    def _symbol_instance(self, lib_id, ref, value, footprint, x, y, rot,
                          unit=1, mirror=None, is_power=False, dnp=False,
                          no_sim=False):
        sym_uuid = gen_uuid()

        # Get pin numbers from atlas
        atlas_key = lib_id
        pin_data = self.atlas.get(atlas_key, {})
        if isinstance(pin_data, dict) and '_meta' not in pin_data:
            pin_numbers = sorted(pin_data.keys(),
                                 key=lambda x: (int(x) if x.isdigit() else 999, x))
        else:
            pin_numbers = []

        mirror_str = f'\n\t\t(mirror {mirror})' if mirror else ''

        ref_hide = '\n\t\t\t\t(hide yes)' if is_power else ''
        val_hide = '\n\t\t\t\t(hide yes)' if is_power else ''
        ref_size = '0.762 0.762' if is_power else '1.27 1.27'
        val_size = '0.762 0.762' if is_power else '1.27 1.27'

        ref_x = x + 2 if not is_power else x
        ref_y = y

        out = f'''\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} {rot})
\t\t(unit {unit}){mirror_str}
\t\t(exclude_from_sim {'yes' if no_sim else 'no'})
\t\t(in_bom {'no' if is_power else 'yes'})
\t\t(on_board {'no' if is_power else 'yes'})
\t\t(dnp {'yes' if dnp else 'no'})
\t\t(uuid "{sym_uuid}")
\t\t(property "Reference" "{ref}"
\t\t\t(at {ref_x} {ref_y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size {ref_size})
\t\t\t\t){ref_hide}
\t\t\t)
\t\t)
\t\t(property "Value" "{value}"
\t\t\t(at {x} {y + 2} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size {val_size})
\t\t\t\t){val_hide}
\t\t\t)
\t\t)
\t\t(property "Footprint" "{footprint}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(hide yes)
\t\t\t)
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(hide yes)
\t\t\t)
\t\t)
\t\t(property "Description" ""
\t\t\t(at {x} {y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(hide yes)
\t\t\t)
\t\t)'''

        for pnum in pin_numbers:
            out += f'''
\t\t(pin "{pnum}"
\t\t\t(uuid "{gen_uuid()}")
\t\t)'''

        out += f'''
\t\t(instances
\t\t\t(project ""
\t\t\t\t(path "/{self.project_uuid}"
\t\t\t\t\t(reference "{ref}")
\t\t\t\t\t(unit {unit})
\t\t\t\t)
\t\t\t)
\t\t)
\t)'''

        return out

    def _resolve_lib_id(self, symbol_short):
        if ':' in symbol_short:
            return symbol_short
        defaults = {
            'R': 'Device:R', 'C': 'Device:C', 'CP': 'Device:CP',
            'L': 'Device:L', 'D': 'Device:D',
            'D_Schottky': 'Device:D_Schottky', 'D_Zener': 'Device:D_Zener',
            'LED': 'Device:LED',
        }
        return defaults.get(symbol_short, f'Device:{symbol_short}')

    def _components(self):
        out = ''
        for comp in self.d.components:
            lib_id = self._resolve_lib_id(comp['symbol'])
            x, y, rot = comp['at']
            fp = comp.get('footprint', '')

            # Footprint fallback: if not in .dsch, look up in component mapping
            if not fp and lib_id in self.fp_map:
                fp = self.fp_map[lib_id]

            if fp and ':' not in fp:
                if fp.startswith('R_'):
                    fp = f'Resistor_THT:{fp}'
                elif fp.startswith('C_'):
                    fp = f'Capacitor_THT:{fp}'

            out += '\n' + self._symbol_instance(
                lib_id=lib_id, ref=comp['ref'], value=comp['value'],
                footprint=fp, x=x, y=y, rot=rot,
                unit=comp.get('unit', 1), mirror=comp.get('mirror'),
                is_power=False, dnp=comp.get('dnp', False),
                no_sim=comp.get('no_sim', False),
            )
        return out

    def _power_symbols(self):
        out = ''
        for pwr in self.d.power_symbols:
            self.pwr_counter += 1
            lib_id = f'power:{pwr["type"]}'
            ref = f'#PWR{self.pwr_counter:02d}'
            out += '\n' + self._symbol_instance(
                lib_id=lib_id, ref=ref, value=pwr['type'],
                footprint='', x=pwr['x'], y=pwr['y'], rot=0,
                is_power=True,
            )
        return out

    def _sheets(self):
        out = ''
        for sheet in self.d.sheets:
            x, y, rot = sheet['at']
            out += f'''
\t(sheet
\t\t(at {x} {y})
\t\t(size 20 10)
\t\t(fields_autoplaced yes)
\t\t(stroke
\t\t\t(width 0.1524)
\t\t\t(type solid)
\t\t)
\t\t(fill
\t\t\t(color 0 0 0 0.0000)
\t\t)
\t\t(uuid "{gen_uuid()}")
\t\t(property "Sheetname" "{sheet['name']}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(justify left bottom)
\t\t\t)
\t\t)
\t\t(property "Sheetfile" "{sheet['file']}"
\t\t\t(at {x} {y + 11} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(justify left top)
\t\t\t)
\t\t)
\t)'''
        return out

    def _sheet_instances(self):
        return f'''
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)'''


# ─── Main ────────────────────────────────────────────────────────────────────

def gen_sch(dsch_path, atlas_path=None, output_path=None, lib_dir=None,
            mapping_path=None):
    """Main generation function."""

    if not os.path.exists(dsch_path):
        print(f"Error: {dsch_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(dsch_path, 'r') as f:
        dsch_text = f.read()

    dsch = DschParser(dsch_text)

    # Load atlas
    atlas = {}
    if atlas_path:
        if os.path.exists(atlas_path):
            with open(atlas_path, 'r') as f:
                atlas = json.load(f)
            print(f"Atlas: {len(atlas) - 1} symbols")
    else:
        for candidate in ['kicad_pin_atlas.json', '../kicad_pin_atlas.json',
                          os.path.expanduser('~/kicad_pin_atlas.json')]:
            if os.path.exists(candidate):
                with open(candidate, 'r') as f:
                    atlas = json.load(f)
                print(f"Atlas: {candidate} ({len(atlas) - 1} symbols)")
                break

    # Load component mapping for footprints
    fp_map = load_component_mapping(mapping_path)
    if fp_map:
        print(f"Footprint mapping: {len(fp_map)} entries")
        for lib_id, fp in sorted(fp_map.items()):
            print(f"  {lib_id} -> {fp}")

    # Find lib dir
    if not lib_dir:
        lib_dir = find_lib_dir()
    if lib_dir:
        print(f"Libraries: {lib_dir}")
    else:
        print("WARNING: No KiCad symbol libraries found!")

    if not output_path:
        base = os.path.splitext(dsch_path)[0]
        output_path = base + '.kicad_sch'

    writer = KicadSchWriter(dsch, atlas, lib_dir, fp_map)
    content = writer.generate()

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    size = os.path.getsize(output_path)
    print(f"\nGenerated: {output_path} ({size:,} bytes)")
    print(f"  Components: {len(dsch.components)}")
    print(f"  Power symbols: {len(dsch.power_symbols)}")
    print(f"  Wires: {len(dsch.wires)}")
    print(f"  Labels: {len(dsch.labels)}")

    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Generate .kicad_sch from distilled schematic format.')
    parser.add_argument('input', help='Input .dsch file')
    parser.add_argument('-a', '--atlas', default=None,
                        help='Path to kicad_pin_atlas.json')
    parser.add_argument('-l', '--libdir', default=None,
                        help='Path to KiCad symbol libraries directory')
    parser.add_argument('-m', '--mapping', default=None,
                        help='Path to component_mapping.json (for footprints)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output .kicad_sch path')
    args = parser.parse_args()

    gen_sch(args.input, args.atlas, args.output, args.libdir, args.mapping)


if __name__ == '__main__':
    main()