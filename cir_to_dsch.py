#!/usr/bin/env python3
"""
cir_to_dsch.py - Convert a SPICE netlist (.cir) to distilled schematic (.dsch).

Reads a SPICE netlist, flattens subcircuit instances, maps components to KiCad
symbols using a component mapping file, and outputs a .dsch file with:
  - Components placed on a grid
  - Every pin labelled with its net name
  - Power symbols on power nets (GND, VCC, +12V)

NO wire routing is performed. The output is a netlist expressed as labels,
intended for an LLM or human to add wires and tidy layout.

Only components whose SPICE model appears in the component mapping are included.
Only primitives (R, C, L) from inside expanded subcircuits are included.
Top-level primitives and voltage sources are test infrastructure and are skipped.

Required inputs:
    - .cir file (SPICE netlist with .subckt definitions)
    - component_mapping.json (SPICE model name -> KiCad lib_id)
    - kicad_pin_atlas.json (KiCad pin positions)

Usage:
    python3 cir_to_dsch.py input.cir -m component_mapping.json -a kicad_pin_atlas.json
    python3 cir_to_dsch.py input.cir -m component_mapping.json -a kicad_pin_atlas.json -o output.dsch
"""

import sys
import os
import re
import json
import argparse
from collections import defaultdict


# ─── SPICE Parser ─────────────────────────────────────────────────────────────

class SpiceSubckt:
    """A parsed .SUBCKT definition."""
    def __init__(self, name, pins, body_lines):
        self.name = name
        self.pins = pins
        self.body_lines = body_lines


class SpiceComponent:
    """A parsed component instance (after flattening)."""
    def __init__(self, ref, comp_type, nodes, model=None, value=None):
        self.ref = ref
        self.comp_type = comp_type
        self.nodes = nodes
        self.model = model
        self.value = value


class SpiceParser:
    """Parse a SPICE netlist, handling .include, .subckt, continuation lines."""

    def __init__(self):
        self.subcircuits = {}
        self.top_components = []
        self.include_dirs = []

    def parse_file(self, filepath):
        self.include_dirs.append(os.path.dirname(os.path.abspath(filepath)))
        with open(filepath, 'r') as f:
            text = f.read()
        self._parse_text(text)

    def _resolve_include(self, filename):
        for d in self.include_dirs:
            path = os.path.join(d, filename)
            if os.path.exists(path):
                return path
        return None

    def _join_continuation_lines(self, text):
        lines = text.split('\n')
        joined = []
        for line in lines:
            stripped = line.rstrip()
            if stripped.startswith('+'):
                if joined:
                    joined[-1] += ' ' + stripped[1:].strip()
                else:
                    joined.append(stripped[1:].strip())
            else:
                joined.append(stripped)
        return joined

    def _parse_text(self, text):
        lines = self._join_continuation_lines(text)
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('*'):
                i += 1
                continue

            lower = line.lower()

            if lower.startswith('.include'):
                filename = line.split(None, 1)[1].strip().strip('"\'')
                inc_path = self._resolve_include(filename)
                if inc_path:
                    with open(inc_path, 'r') as f:
                        self._parse_text(f.read())
                else:
                    print(f"  WARNING: Cannot find include file: {filename}",
                          file=sys.stderr)
                i += 1
                continue

            if lower.startswith('.subckt'):
                body_lines = []
                parts = line.split()
                name = parts[1]
                pins = parts[2:]
                i += 1
                while i < len(lines):
                    sub_line = lines[i].strip()
                    if sub_line.lower().startswith('.ends'):
                        break
                    body_lines.append(sub_line)
                    i += 1
                self.subcircuits[name] = SpiceSubckt(name, pins, body_lines)
                i += 1
                continue

            if lower.startswith(('.tran', '.ac', '.dc', '.op', '.control',
                                 '.endc', '.end', '.model', '.param',
                                 '.option', '.save', '.meas', '.global',
                                 '.lib', '.temp', '.ic', '.nodeset')):
                if lower.startswith('.control'):
                    i += 1
                    while i < len(lines) and not lines[i].strip().lower().startswith('.endc'):
                        i += 1
                i += 1
                continue

            comp = self._parse_component_line(line)
            if comp:
                self.top_components.append(comp)
            i += 1

    def _parse_component_line(self, line):
        parts = line.split()
        if not parts:
            return None

        ref = parts[0]
        first_char = ref[0].upper()

        if first_char == 'R' and len(parts) >= 4:
            return SpiceComponent(ref, 'R', [parts[1], parts[2]],
                                  model='R', value=parts[3])
        elif first_char == 'C' and len(parts) >= 4:
            return SpiceComponent(ref, 'C', [parts[1], parts[2]],
                                  model='C', value=parts[3])
        elif first_char == 'L' and len(parts) >= 4:
            return SpiceComponent(ref, 'L', [parts[1], parts[2]],
                                  model='L', value=parts[3])
        elif first_char == 'D' and len(parts) >= 4:
            return SpiceComponent(ref, 'D', [parts[1], parts[2]],
                                  model=parts[3])
        elif first_char == 'Q' and len(parts) >= 5:
            return SpiceComponent(ref, 'Q', [parts[1], parts[2], parts[3]],
                                  model=parts[4])
        elif first_char == 'V' and len(parts) >= 3:
            return SpiceComponent(ref, 'V', [parts[1], parts[2]],
                                  model='V', value=' '.join(parts[3:]))
        elif first_char == 'I' and len(parts) >= 3:
            return SpiceComponent(ref, 'I', [parts[1], parts[2]],
                                  model='I', value=' '.join(parts[3:]))
        elif first_char == 'X' and len(parts) >= 3:
            return SpiceComponent(ref, 'X', parts[1:-1], model=parts[-1])
        return None


# ─── Flattener ────────────────────────────────────────────────────────────────

class Flattener:
    """Flatten subcircuit instances into primitive components."""

    def __init__(self, subcircuits, component_mapping):
        self.subcircuits = subcircuits
        self.mapping = component_mapping
        self.flat_components = []

    def flatten(self, top_components):
        for comp in top_components:
            self._flatten_component(comp, prefix='', net_map={},
                                    from_subckt=False)
        return self.flat_components

    def _flatten_component(self, comp, prefix, net_map, from_subckt=False):
        mapped_nodes = [net_map.get(n, n) for n in comp.nodes]

        if comp.comp_type == 'X' and comp.model in self.subcircuits:
            subckt = self.subcircuits[comp.model]

            if comp.model in self.mapping:
                self.flat_components.append(SpiceComponent(
                    ref=comp.ref, comp_type='X', nodes=mapped_nodes,
                    model=comp.model))
                return

            inst_prefix = f"{prefix}{comp.ref}_"
            sub_net_map = {}
            for i, pin_name in enumerate(subckt.pins):
                if i < len(mapped_nodes):
                    sub_net_map[pin_name] = mapped_nodes[i]

            parser = SpiceParser()
            for line in subckt.body_lines:
                if not line or line.startswith('*') or line.lower().startswith('.'):
                    continue
                sub_comp = parser._parse_component_line(line)
                if sub_comp:
                    sub_comp.ref = inst_prefix + sub_comp.ref
                    new_nodes = []
                    for node in sub_comp.nodes:
                        if node in sub_net_map:
                            new_nodes.append(sub_net_map[node])
                        elif node == '0' or node.lower() == 'gnd':
                            new_nodes.append('0')
                        else:
                            new_nodes.append(inst_prefix + node)
                    sub_comp.nodes = new_nodes
                    self._flatten_component(sub_comp, inst_prefix,
                                            net_map={}, from_subckt=True)

        elif comp.comp_type in ('R', 'C', 'L', 'D', 'Q'):
            if not from_subckt:
                return
            if comp.model in self.mapping or comp.comp_type in self.mapping:
                self.flat_components.append(SpiceComponent(
                    ref=comp.ref, comp_type=comp.comp_type,
                    nodes=mapped_nodes, model=comp.model, value=comp.value))

        elif comp.comp_type == 'X' and comp.model in self.mapping:
            self.flat_components.append(SpiceComponent(
                ref=comp.ref, comp_type='X', nodes=mapped_nodes,
                model=comp.model))


# ─── Pin Mapper ───────────────────────────────────────────────────────────────

class PinMapper:
    """Map SPICE node indices to KiCad pin numbers and compute positions."""

    def __init__(self, subcircuits, mapping, atlas):
        self.subcircuits = subcircuits
        self.mapping = mapping
        self.atlas = atlas

    def get_kicad_pin(self, comp, node_index):
        if comp.comp_type in ('R', 'C', 'L', 'D'):
            return str(node_index + 1)

        if comp.comp_type == 'X' and comp.model in self.subcircuits:
            lib_id = self.mapping.get(comp.model)
            if not lib_id:
                return str(node_index + 1)

            entry = self.atlas.get(lib_id, {})
            pin_numbers = sorted(
                [k for k in entry.keys() if k != '_meta'],
                key=lambda x: (int(x) if x.isdigit() else 999, x))

            if node_index < len(pin_numbers):
                return pin_numbers[node_index]

        return str(node_index + 1)

    def get_pin_position(self, comp, node_index, origin):
        lib_id = self._get_lib_id(comp)
        entry = self.atlas.get(lib_id, {})
        kicad_pin = self.get_kicad_pin(comp, node_index)

        if kicad_pin and kicad_pin in entry:
            dx, dy = entry[kicad_pin]
            return (round(origin[0] + dx, 2), round(origin[1] - dy, 2))
        return origin

    def _get_lib_id(self, comp):
        if comp.model in self.mapping:
            return self.mapping[comp.model]
        type_map = {'R': 'Device:R', 'C': 'Device:C', 'L': 'Device:L',
                     'D': 'Device:D'}
        return type_map.get(comp.comp_type, '')


# ─── Layout Engine ────────────────────────────────────────────────────────────

class LayoutEngine:
    """Simple grid-based auto-placement."""

    def __init__(self, atlas, mapping):
        self.atlas = atlas
        self.mapping = mapping

    def place(self, components):
        placements = {}
        x, y = 50, 50
        row_height = 0
        max_x = 250

        for comp in components:
            lib_id = self._get_lib_id(comp)
            bounds = self._get_bounds(lib_id)
            spacing = 60 if comp.comp_type == 'X' else 30

            if x + spacing > max_x:
                x = 50
                y += row_height + 50
                row_height = 0

            placements[comp.ref] = (round(x, 2), round(y, 2))
            x += spacing
            row_height = max(row_height, bounds[1])

        return placements

    def _get_lib_id(self, comp):
        if comp.model in self.mapping:
            return self.mapping[comp.model]
        type_map = {'R': 'Device:R', 'C': 'Device:C', 'L': 'Device:L',
                     'D': 'Device:D'}
        return type_map.get(comp.comp_type, '')

    def _get_bounds(self, lib_id):
        entry = self.atlas.get(lib_id, {})
        if not entry:
            return (20, 20)
        xs = [v[0] for v in entry.values() if isinstance(v, list)]
        ys = [v[1] for v in entry.values() if isinstance(v, list)]
        if not xs:
            return (20, 20)
        return (max(xs) - min(xs) + 10, max(ys) - min(ys) + 10)


# ─── DSCH Emitter ─────────────────────────────────────────────────────────────

class DschEmitter:
    """Generate .dsch output with labels-only connectivity (no wires)."""

    POWER_NETS = {
        '0': 'GND', 'gnd': 'GND',
        'vcc': 'VCC', 'vdd': 'VCC',
        'vm': '+12V',
    }

    def __init__(self, mapping, pin_mapper):
        self.mapping = mapping
        self.pin_mapper = pin_mapper

    def emit(self, components, placements, paper='A3', title=''):
        lines = []
        L = lines.append

        L(f"HDR:v1:gen=cir_to_dsch:1.0:paper={paper}")
        L(f'TITLE:title="{title}":date="":rev="1.0":company=""')
        L("")

        # Assign clean reference designators
        ref_counters = defaultdict(int)
        comp_data = []
        for comp in components:
            lib_id = self._get_lib_id(comp)
            if not lib_id:
                continue
            new_ref = self._make_ref(comp, ref_counters)
            origin = placements.get(comp.ref, (100, 100))
            comp_data.append((comp, new_ref, lib_id, origin))

        # Symbol declarations
        lib_ids_used = set(lib_id for _, _, lib_id, _ in comp_data)
        for lib_id in sorted(lib_ids_used):
            L(f"SYM:{lib_id}:pins[]")
        L("")

        # Components
        L("# COMPONENTS")
        for comp, new_ref, lib_id, origin in comp_data:
            value = comp.value or lib_id.split(':')[-1]
            L(f"COMP:{new_ref}:{lib_id}:val={value}:@({origin[0]},{origin[1]})")
        L("")

        # Pin labels and power symbols
        power_symbols = []

        L("# NET LABELS (per component, per pin)")
        for comp, new_ref, lib_id, origin in comp_data:
            L(f"# {new_ref} ({lib_id})")
            for i, net_name in enumerate(comp.nodes):
                pos = self.pin_mapper.get_pin_position(comp, i, origin)
                kicad_pin = self.pin_mapper.get_kicad_pin(comp, i)
                pwr = self.POWER_NETS.get(net_name.lower())

                if pwr:
                    if pwr == 'GND':
                        sym_pos = (pos[0], pos[1] + 5)
                    else:
                        sym_pos = (pos[0], pos[1] - 5)
                    power_symbols.append((pwr, sym_pos, pos))
                else:
                    L(f'LBL:"{net_name}"@({pos[0]},{pos[1]})')
        L("")

        # Power symbols (deduplicated)
        L("# POWER SYMBOLS")
        seen = set()
        for pwr_type, sym_pos, pin_pos in power_symbols:
            key = (pwr_type, sym_pos[0], sym_pos[1])
            if key not in seen:
                seen.add(key)
                L(f"PWR:{pwr_type}:({sym_pos[0]},{sym_pos[1]})")
        L("")

        # Stub wires: pin to power symbol
        L("# POWER STUB WIRES")
        for pwr_type, sym_pos, pin_pos in power_symbols:
            L(f"W:({pin_pos[0]},{pin_pos[1]})->({sym_pos[0]},{sym_pos[1]})")
        L("")

        return '\n'.join(lines)

    def _get_lib_id(self, comp):
        if comp.model in self.mapping:
            return self.mapping[comp.model]
        type_map = {'R': 'Device:R', 'C': 'Device:C', 'L': 'Device:L',
                     'D': 'Device:D'}
        return type_map.get(comp.comp_type, '')

    def _make_ref(self, comp, counters):
        prefix_map = {
            'R': 'R', 'C': 'C', 'L': 'L', 'D': 'D', 'Q': 'Q',
            'X': 'U', 'V': 'V', 'I': 'I',
        }
        prefix = prefix_map.get(comp.comp_type, 'U')
        counters[prefix] += 1
        return f"{prefix}{counters[prefix]}"


# ─── Main ────────────────────────────────────────────────────────────────────

def cir_to_dsch(cir_path, mapping_path, atlas_path, output_path=None,
                paper='A3', title=''):

    if not os.path.exists(cir_path):
        print(f"Error: {cir_path} not found", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(mapping_path):
        print(f"Error: {mapping_path} not found", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(atlas_path):
        print(f"Error: {atlas_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(mapping_path, 'r') as f:
        raw_mapping = json.load(f)

    # Normalize mapping: support both old format (flat strings) and new format (objects)
    #   Old: {"74HC4094": "4xxx:HEF4094B"}
    #   New: {"74HC4094": {"lib_id": "4xxx:HEF4094B", "footprint": "..."}}
    mapping = {}
    for key, val in raw_mapping.items():
        if isinstance(val, dict):
            mapping[key] = val.get('lib_id', '')
        else:
            mapping[key] = val
    print(f"Component mapping: {len(mapping)} entries")

    with open(atlas_path, 'r') as f:
        atlas = json.load(f)
    atlas_count = len(atlas) - 1 if '_meta' in atlas else len(atlas)
    print(f"Pin atlas: {atlas_count} symbols")

    for spice_model, lib_id in mapping.items():
        if lib_id and lib_id not in atlas:
            print(f"  WARNING: {lib_id} (mapped from {spice_model}) "
                  f"not found in pin atlas", file=sys.stderr)

    # Parse
    parser = SpiceParser()
    parser.parse_file(cir_path)
    print(f"Parsed: {len(parser.top_components)} top-level components, "
          f"{len(parser.subcircuits)} subcircuit definitions")

    for name in parser.subcircuits:
        mapped = "-> " + mapping[name] if name in mapping else "(not mapped, will expand)"
        print(f"  .subckt {name} {mapped}")

    # Flatten
    flattener = Flattener(parser.subcircuits, mapping)
    flat_components = flattener.flatten(parser.top_components)
    print(f"Flattened: {len(flat_components)} components")

    for comp in flat_components:
        lib_id = mapping.get(comp.model, mapping.get(comp.comp_type, '?'))
        print(f"  {comp.ref:20s} type={comp.comp_type} model={comp.model:15s} "
              f"-> {lib_id}  nets={comp.nodes}")

    # Layout
    layout = LayoutEngine(atlas, mapping)
    placements = layout.place(flat_components)

    # Pin mapper
    pin_mapper = PinMapper(parser.subcircuits, mapping, atlas)

    # Emit
    emitter = DschEmitter(mapping, pin_mapper)
    dsch_text = emitter.emit(flat_components, placements, paper=paper,
                              title=title)

    if not output_path:
        base = os.path.splitext(cir_path)[0]
        output_path = base + '.dsch'

    with open(output_path, 'w') as f:
        f.write(dsch_text)

    label_count = dsch_text.count('\nLBL:')
    pwr_count = dsch_text.count('\nPWR:')
    wire_count = dsch_text.count('\nW:')

    print(f"\nGenerated: {output_path}")
    print(f"  Components: {len(flat_components)}")
    print(f"  Labels: {label_count}")
    print(f"  Power symbols: {pwr_count}")
    print(f"  Wires: {wire_count} (power stubs only)")

    return output_path


def main():
    p = argparse.ArgumentParser(
        description='Convert SPICE .cir netlist to .dsch distilled schematic.')
    p.add_argument('input', help='Input .cir file')
    p.add_argument('-m', '--mapping', required=True,
                   help='Path to component_mapping.json')
    p.add_argument('-a', '--atlas', required=True,
                   help='Path to kicad_pin_atlas.json')
    p.add_argument('-o', '--output', default=None,
                   help='Output .dsch path')
    p.add_argument('-p', '--paper', default='A3',
                   help='Paper size (default: A3)')
    p.add_argument('-t', '--title', default='',
                   help='Schematic title')
    args = p.parse_args()

    cir_to_dsch(args.input, args.mapping, args.atlas, args.output,
                args.paper, args.title)


if __name__ == '__main__':
    main()