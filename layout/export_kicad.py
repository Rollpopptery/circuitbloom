"""
export_kicad.py — Push CircuitBloom layout to KiCad

Reads layout state from stdin (sent by layout_server.py).
Uses tree_to_xy.py for the transform.
Uses kipy to move and rotate components in a running KiCad instance.
"""

import sys
import json
import math
import time

import kipy
from kipy.common_types import Vector2
from kipy.geometry import Angle
from tree_to_xy import transform


def main():
    # Read layout state from stdin
    raw = sys.stdin.read()
    state = json.loads(raw)

    tree = state.get("tree")
    if not tree:
        print("No tree loaded.", file=sys.stderr)
        sys.exit(1)

    # The transform — tree to mm centre positions
    positions = transform(tree)

    # Component table — for rotations
    comp_table = state.get("components", {})

    # Connect to KiCad
    print("Connecting to KiCad...")
    kicad = kipy.KiCad()
    board = kicad.get_board()
    footprints = board.get_footprints()

    # Build lookup by reference
    fp_lookup = {}
    for fp in footprints:
        ref = fp.reference_field.text.value
        fp_lookup[ref] = fp

    # Find board origin from board outline (Edge.Cuts)
    shapes = board.get_shapes()
    edge_x = []
    edge_y = []
    for s in shapes:
        edge_x.extend([s.start.x, s.end.x])
        edge_y.extend([s.start.y, s.end.y])

    if edge_x and edge_y:
        origin_x = min(edge_x)
        origin_y = min(edge_y)
    else:
        print("  WARNING: no board outline found, using (0,0)")
        origin_x = 0
        origin_y = 0

    print(f"Board origin: ({origin_x/1e6:.2f}, {origin_y/1e6:.2f}) mm")

    # Move and rotate each component one at a time
    count = 0
    for name, (x_mm, y_mm) in positions.items():
        if name not in fp_lookup:
            print(f"  WARNING: {name} not in KiCad")
            continue

        fp = fp_lookup[name]

        # Position (centres, in nanometres) with optional offset in local frame
        comp = comp_table.get(name, {})
        ox, oy = comp.get("offset", [0, 0])
        rotation = comp.get("rotation", 0)
        rad = math.radians(rotation)
        board_ox = ox * math.cos(rad) + oy * math.sin(rad)
        board_oy = -ox * math.sin(rad) + oy * math.cos(rad)
        new_x = origin_x + int((x_mm + board_ox) * 1e6)
        new_y = origin_y + int((y_mm + board_oy) * 1e6)
        fp.position = Vector2.from_xy(new_x, new_y)

        # Rotation from component table
        rotation = 0
        if name in comp_table:
            rotation = comp_table[name].get("rotation", 0)
        fp.orientation = Angle.from_degrees(rotation)

        board.update_items([fp])
        count += 1
        rot_str = f" rot={rotation}" if rotation else ""
        print(f"  {name:6s} -> ({x_mm:.2f}, {y_mm:.2f}) mm{rot_str}")
        time.sleep(0.03)

    print(f"Moved {count} components in KiCad.")


if __name__ == '__main__':
    main()
