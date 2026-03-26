#!/usr/bin/env python3
"""
gen_pcb_from_bloom.py — Generate a .kicad_pcb from a .bloom file (no base needed)

Creates a complete KiCad PCB file with footprint geometry generated from
the package types defined in the .bloom file.

Usage:
    python3 gen_pcb_from_bloom.py design.bloom -o output.kicad_pcb
"""

import json
import sys
import uuid
import argparse
from pathlib import Path

VIEWPORT_X = 120.0
VIEWPORT_Y = 65.0


def bpc(x, y):
    """Board-space to KiCad PCB coordinate."""
    return round(x + VIEWPORT_X, 4), round(y + VIEWPORT_Y, 4)


def uid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Footprint geometry library — pad shapes per package type
# ---------------------------------------------------------------------------

FOOTPRINT_LIB = {
    "0402": {
        "lib": "Capacitor_SMD:C_0402_1005Metric",
        "pads": [
            {"num": "1", "type": "smd", "shape": "roundrect", "at": [-0.48, 0], "size": [0.56, 0.62], "layers": "F"},
            {"num": "2", "type": "smd", "shape": "roundrect", "at": [0.48, 0], "size": [0.56, 0.62], "layers": "F"},
        ],
        "courtyard": [-0.96, -0.51, 0.96, 0.51],
        "body": [-0.5, -0.25, 0.5, 0.25],
    },
    "1812": {
        "lib": "Resistor_SMD:R_1812_4532Metric",
        "pads": [
            {"num": "1", "type": "smd", "shape": "roundrect", "at": [-2.2, 0], "size": [1.15, 3.35], "layers": "F"},
            {"num": "2", "type": "smd", "shape": "roundrect", "at": [2.2, 0], "size": [1.15, 3.35], "layers": "F"},
        ],
        "courtyard": [-3.0, -1.9, 3.0, 1.9],
        "body": [-2.25, -1.6, 2.25, 1.6],
    },
    "2512": {
        "lib": "Resistor_SMD:R_2512_6332Metric",
        "pads": [
            {"num": "1", "type": "smd", "shape": "roundrect", "at": [-3.0, 0], "size": [1.4, 3.35], "layers": "F"},
            {"num": "2", "type": "smd", "shape": "roundrect", "at": [3.0, 0], "size": [1.4, 3.35], "layers": "F"},
        ],
        "courtyard": [-3.95, -1.9, 3.95, 1.9],
        "body": [-3.15, -1.6, 3.15, 1.6],
    },
    "SOT-23": {
        "lib": "Package_TO_SOT_SMD:SOT-23",
        "pads": [
            {"num": "1", "type": "smd", "shape": "roundrect", "at": [-0.95, 1.1], "size": [0.6, 0.7], "layers": "F"},
            {"num": "2", "type": "smd", "shape": "roundrect", "at": [0.95, 1.1], "size": [0.6, 0.7], "layers": "F"},
            {"num": "3", "type": "smd", "shape": "roundrect", "at": [0, -1.1], "size": [0.6, 0.7], "layers": "F"},
        ],
        "courtyard": [-1.45, -1.65, 1.45, 1.65],
        "body": [-0.65, -0.85, 0.65, 0.85],
    },
    "SOT-23-5": {
        "lib": "Package_TO_SOT_SMD:SOT-23-5",
        "pads": [
            {"num": "1", "type": "smd", "shape": "roundrect", "at": [-0.95, 0.8], "size": [0.6, 0.4], "layers": "F"},
            {"num": "2", "type": "smd", "shape": "roundrect", "at": [0, 0.8], "size": [0.6, 0.4], "layers": "F"},
            {"num": "3", "type": "smd", "shape": "roundrect", "at": [0.95, 0.8], "size": [0.6, 0.4], "layers": "F"},
            {"num": "4", "type": "smd", "shape": "roundrect", "at": [0.95, -0.8], "size": [0.6, 0.4], "layers": "F"},
            {"num": "5", "type": "smd", "shape": "roundrect", "at": [-0.95, -0.8], "size": [0.6, 0.4], "layers": "F"},
        ],
        "courtyard": [-1.45, -1.2, 1.45, 1.2],
        "body": [-0.65, -0.65, 0.65, 0.65],
    },
    "SOIC-20": {
        "lib": "Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm",
        "pads": [],  # generated below
        "courtyard": [-4.3, -6.55, 4.3, 6.55],
        "body": [-3.75, -6.4, 3.75, 6.4],
    },
    "JST-PH-2": {
        "lib": "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
        "pads": [
            {"num": "1", "type": "thru_hole", "shape": "rect", "at": [0, 0], "size": [1.2, 1.2], "drill": 0.75, "layers": "*.Cu"},
            {"num": "2", "type": "thru_hole", "shape": "oval", "at": [-2.0, 0], "size": [1.2, 1.2], "drill": 0.75, "layers": "*.Cu"},
        ],
        "courtyard": [-3.2, -1.8, 1.2, 1.8],
        "body": [-3.0, -1.5, 1.0, 1.5],
    },
    "CONN_2PIN": {
        "lib": "Connector_Generic:Conn_01x02_P2.00mm",
        "pads": [
            {"num": "1", "type": "thru_hole", "shape": "rect", "at": [0, 0], "size": [1.2, 1.2], "drill": 0.75, "layers": "*.Cu"},
            {"num": "2", "type": "thru_hole", "shape": "oval", "at": [-2.0, 0], "size": [1.2, 1.2], "drill": 0.75, "layers": "*.Cu"},
        ],
        "courtyard": [-3.2, -1.8, 1.2, 1.8],
        "body": [-3.0, -1.5, 1.0, 1.5],
    },
}

# Generate SOIC-20 pads
_soic_pads = []
for i in range(10):
    y = -5.715 + i * 1.27
    _soic_pads.append({"num": str(i + 1), "type": "smd", "shape": "roundrect",
                        "at": [-3.75, round(y, 3)], "size": [1.5, 0.6], "layers": "F"})
for i in range(10):
    y = 5.715 - i * 1.27
    _soic_pads.append({"num": str(i + 11), "type": "smd", "shape": "roundrect",
                        "at": [3.75, round(y, 3)], "size": [1.5, 0.6], "layers": "F"})
FOOTPRINT_LIB["SOIC-20"]["pads"] = _soic_pads


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
        lines.append(f'\t\t\t(roundrect_rratio 0.25)')
    lines.append(f'\t\t\t(layers {layer_str})')
    if net_num > 0:
        lines.append(f'\t\t\t(net {net_num} "{net_name}")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t)')
    return "\n".join(lines)


def gen_footprint(ref, comp, package, net_map, board_x, board_y, rotation=0):
    """Generate a complete footprint s-expression."""
    fp_def = FOOTPRINT_LIB.get(package)
    if not fp_def:
        print(f"  WARNING: no footprint geometry for package '{package}', skipping {ref}",
              file=sys.stderr)
        return ""

    lib = fp_def["lib"]
    px, py = bpc(board_x, board_y)
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
    lines.append(f'\t\t(property "Reference" "{ref}"')
    lines.append(f'\t\t\t(at 0 {fp_def["courtyard"][1] - 1}{rot_str})')
    lines.append(f'\t\t\t(layer "F.SilkS")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append(f'\t\t)')

    value = comp.get("value", comp.get("type", ""))
    lines.append(f'\t\t(property "Value" "{value}"')
    lines.append(f'\t\t\t(at 0 {fp_def["courtyard"][3] + 1}{rot_str})')
    lines.append(f'\t\t\t(layer "F.Fab")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append(f'\t\t)')

    # Courtyard
    cx1, cy1, cx2, cy2 = fp_def["courtyard"]
    lines.append(f'\t\t(fp_rect')
    lines.append(f'\t\t\t(start {cx1} {cy1})')
    lines.append(f'\t\t\t(end {cx2} {cy2})')
    lines.append(f'\t\t\t(stroke (width 0.05) (type solid))')
    lines.append(f'\t\t\t(fill none)')
    lines.append(f'\t\t\t(layer "F.CrtYd")')
    lines.append(f'\t\t\t(uuid "{uid()}")')
    lines.append(f'\t\t)')

    # Body outline on Fab
    bx1, by1, bx2, by2 = fp_def["body"]
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_pcb(bloom):
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

    # PCB positions — use reasonable layout if viewer positions are too large
    # Check if positions look like PCB mm (< 200) or viewer coords (> 200)
    positions = {}
    max_coord = 0
    for ref, comp in components.items():
        pos = comp.get("position", {})
        x, y = pos.get("x", 0), pos.get("y", 0)
        max_coord = max(max_coord, abs(x), abs(y))
        positions[ref] = (x, y)

    if max_coord > 200:
        # Viewer coordinates — scale to fit board
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        x_range = x_max - x_min or 1
        y_range = y_max - y_min or 1
        margin = 5
        bw, bh = board[0] - 2 * margin, board[1] - 2 * margin
        scale = min(bw / x_range, bh / y_range)
        for ref in positions:
            ox, oy = positions[ref]
            positions[ref] = (
                round((ox - x_min) * scale + margin, 2),
                round((oy - y_min) * scale + margin, 2)
            )

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
        bx, by = positions.get(ref, (0, 0))
        rotation = comp.get("rotation", 0)
        fp_text = gen_footprint(ref, comp, package, net_map, bx, by, rotation)
        if fp_text:
            lines.append(fp_text)

    # Board outline
    lines.append(gen_board_outline(board[0], board[1]))

    lines.append(')')
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate .kicad_pcb from .bloom")
    parser.add_argument("bloom", help="Input .bloom file")
    parser.add_argument("-o", "--output", required=True, help="Output .kicad_pcb")
    args = parser.parse_args()

    bloom_path = Path(args.bloom)
    if not bloom_path.exists():
        print(f"Error: {bloom_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(bloom_path) as f:
        bloom = json.load(f)

    pcb_text = generate_pcb(bloom)
    Path(args.output).write_text(pcb_text, encoding='utf-8')

    n_comp = len(bloom.get("components", {}))
    print(f"Generated: {args.output} from {bloom_path}", file=sys.stderr)
    print(f"  {n_comp} footprints", file=sys.stderr)


if __name__ == "__main__":
    main()
