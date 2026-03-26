"""
bloom_grid.py — Build a RouterGrid from a .bloom file.

Reads component positions from layout_tree (via tree_to_xy),
pad offsets from pcb.footprints, rotations from components,
and nets from component pin assignments.

Usage:
    from bloom_grid import load_bloom, build_grid

    bloom = load_bloom("path/to/board.bloom")
    grid, pads, nets = build_grid(bloom)
"""

import json
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'layout'))
from tree_to_xy import transform as tree_to_xy
from dpcb_router import RouterGrid, GRID_PITCH, LAYER_IDS


# SMD package prefixes — everything else assumed through-hole
SMD_PACKAGES = {
    '0201', '0402', '0603', '0805', '1206', '1210', '1812', '2010', '2512',
    'SOT-23', 'SOT-23-5', 'SOT-23-6', 'SOT-223', 'SOT-89',
    'SOIC', 'SOP', 'SSOP', 'TSSOP', 'MSOP', 'QFP', 'LQFP', 'TQFP',
    'QFN', 'DFN', 'BGA', 'SOD-123', 'SOD-323', 'SOD-523',
    'RA01',
}


def is_smd_package(package):
    """Check if a package name is SMD."""
    pkg = package.upper()
    for prefix in SMD_PACKAGES:
        if pkg.startswith(prefix.upper()):
            return True
    # SOIC-N pattern
    if pkg.startswith('SOIC'):
        return True
    return False


def rotate_pad(dx, dy, angle_deg):
    """Rotate pad offset — KiCad clockwise convention (Y-down)."""
    a = math.radians(angle_deg)
    cos_a = math.cos(a)
    sin_a = math.sin(a)
    return dx * cos_a + dy * sin_a, -dx * sin_a + dy * cos_a


def load_bloom(path):
    """Load a .bloom JSON file."""
    with open(path) as f:
        return json.load(f)


def get_component_centres(bloom):
    """Get component centre positions from placement or layout_tree.

    Returns {ref: (x_mm, y_mm)}.
    """
    layout = bloom.get("placement") or bloom.get("layout_tree")
    if not layout:
        return {}
    return tree_to_xy(layout)


def get_pad_positions(bloom):
    """Compute absolute pad positions from bloom data.

    Returns {ref: {pin_str: (x_mm, y_mm, is_smd)}}.
    """
    centres = get_component_centres(bloom)
    footprints = bloom.get("pcb", {}).get("footprints", {})
    components = bloom.get("components", {})

    result = {}
    for ref, comp in components.items():
        if ref not in centres:
            continue
        cx, cy = centres[ref]
        package = comp.get("package", "")
        rotation = comp.get("rotation", 0)
        smd = is_smd_package(package)

        pad_offsets = footprints.get(package, {}).get("pads", {})
        pads = {}
        for pin_str, (dx, dy) in pad_offsets.items():
            rdx, rdy = rotate_pad(dx, dy, rotation)
            pads[pin_str] = (cx + rdx, cy + rdy, smd)
        result[ref] = pads

    return result


def get_net_map(bloom):
    """Build net-to-pads mapping from bloom components.

    Returns {net_name: [(ref, pin_str, x_mm, y_mm, is_smd), ...]}.
    """
    pad_positions = get_pad_positions(bloom)
    components = bloom.get("components", {})
    nets = {}

    for ref, comp in components.items():
        if ref not in pad_positions:
            continue
        for pin_str, pin_data in comp.get("pins", {}).items():
            net = pin_data.get("net", "")
            if not net:
                continue
            if pin_str in pad_positions[ref]:
                x, y, smd = pad_positions[ref][pin_str]
                nets.setdefault(net, []).append((ref, pin_str, x, y, smd))

    return nets


def build_grid(bloom):
    """Build a RouterGrid from bloom data.

    Returns (grid, pad_positions, net_map).
    """
    board_dims = bloom.get("pcb", {}).get("board", [55, 45])
    rules = bloom.get("pcb", {}).get("rules", {})

    clearance = rules.get("clearance", 0.2)
    track_w = rules.get("track", 0.25)
    via_drill = rules.get("via_drill", 0.3)
    via_annular = rules.get("via_annular", 0.15)
    via_od = via_drill + 2 * via_annular

    clearance_cells = max(1, int(round(clearance / GRID_PITCH)))
    via_od_cells = max(1, int(round(via_od / GRID_PITCH)))
    via_id_cells = max(1, int(round(via_drill / GRID_PITCH)))

    grid = RouterGrid(board_dims[0], board_dims[1],
                      clearance_cells, via_od_cells, via_id_cells)

    # Build net IDs
    net_map = get_net_map(bloom)
    for i, net_name in enumerate(sorted(net_map.keys())):
        grid.net_ids[net_name] = i + 1

    # Mark pads on grid
    pad_r = 4  # cells (~0.4mm)
    pad_positions = get_pad_positions(bloom)
    via_keepout_r = pad_r + 2

    for ref, pads in pad_positions.items():
        comp = bloom["components"][ref]
        for pin_str, (px, py, smd) in pads.items():
            # Find net ID for this pad
            net = comp.get("pins", {}).get(pin_str, {}).get("net", "")
            nid = grid.net_ids.get(net, 0)
            if nid == 0:
                nid = -1  # unconnected pads block as obstacles

            gx, gy = grid.mm_to_grid(px, py)
            pad_layer = 0 if smd else None  # None = both layers (TH)
            grid.pad_layers[(gx, gy)] = pad_layer

            # Via keepout around all pads
            for dy in range(-via_keepout_r, via_keepout_r + 1):
                for dx in range(-via_keepout_r, via_keepout_r + 1):
                    if dx * dx + dy * dy <= via_keepout_r * via_keepout_r:
                        grid.pad_keepout.add((gx + dx, gy + dy))

            # Mark pad on grid layers
            layers = [0, 1] if not smd else [0]
            for layer in layers:
                grid.mark_pad(px, py, pad_r, layer, nid)

    # Mark existing tracks
    for trk in bloom.get("pcb", {}).get("tracks", []):
        layer = LAYER_IDS.get(trk["layer"], 0)
        nid = grid.net_ids.get(trk["net"], 0)
        w = max(1, int(round(trk.get("width", track_w) / GRID_PITCH)))
        grid.mark_track(trk["x1"], trk["y1"], trk["x2"], trk["y2"],
                        w, layer, nid)

    # Mark existing vias
    for via in bloom.get("pcb", {}).get("vias", []):
        nid = grid.net_ids.get(via["net"], 0)
        grid.mark_via(via["x"], via["y"], nid)

    return grid, pad_positions, net_map


# ============ STANDALONE TEST ============

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bloom_grid.py <path.bloom>")
        sys.exit(1)

    bloom = load_bloom(sys.argv[1])
    grid, pads, nets = build_grid(bloom)

    print(f"Grid: {grid.width}x{grid.height}")
    print(f"Nets: {len(nets)}")
    print(f"Stats: {grid.stats()}")

    print(f"\nPad positions:")
    for ref, pins in sorted(pads.items()):
        for pin, (x, y, smd) in sorted(pins.items()):
            typ = "SMD" if smd else "TH"
            print(f"  {ref}.{pin} @ ({x:.2f}, {y:.2f}) {typ}")

    print(f"\nNets:")
    for net, pad_list in sorted(nets.items()):
        refs = [f"{r}.{p}" for r, p, x, y, s in pad_list]
        print(f"  {net}: {', '.join(refs)}")
