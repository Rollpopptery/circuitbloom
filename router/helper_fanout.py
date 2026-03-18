"""
helper_fanout.py — Determine fan-out stub order for a set of IC pins.

Fan-out routing routes IC pins in two phases:
  1. STUB: short trace perpendicular to the pin row, away from the IC body
  2. TRUNK: long trace from the stub end to the destination

When multiple pins fan out in the same direction, their stubs must be
staggered so the trunks can run parallel without crossing:
  - The pin NEAREST the exit direction gets the SHORTEST stub (inner)
  - The pin FARTHEST from the exit direction gets the LONGEST stub (outer)

Why? The inner pin's trunk leaves first and takes the lane closest to the
pin row. The outer pin's trunk must pass over/under the inner trunk, so it
needs a longer stub to reach the next lane out.

        exit direction: south (↓)

        pin row (vertical, left side of IC):

            U1.4  y=11.4  ←── farthest from south exit = OUTER  = longest stub
            U1.5  y=12.6
            U1.6  y=13.9
            U1.7  y=15.2  ←── nearest to south exit   = INNER  = shortest stub

        stub direction: west (←)  (perpendicular to vertical pin row)

        stubs (looking from above):

            ─────────── U1.4   (longest, x=16.5)
            ────────── U1.5    (x=17.5)
            ──────── U1.6      (x=18.5)
            ────── U1.7        (shortest, x=19.5)

        trunks all run south at their respective x positions,
        parallel, no crossings.

Usage:
    python helper_fanout.py <exit_direction> <stub_spacing> <first_lane_x> <pin1_name>,<x>,<y> <pin2_name>,<x>,<y> ...

    exit_direction:  north | south | east | west
    stub_spacing:    mm between parallel trunk lanes (typically 1.0)
    first_lane:      mm coordinate of the innermost trunk lane

Example:
    python helper_fanout.py south 1.0 18.5 U1.6,21.525,13.905 U1.7,21.525,15.175

Output:
    For each pin: stub endpoint coordinates and routing order.
"""

import sys


def fanout_order(pins, exit_direction, stub_spacing, first_lane):
    """
    pins: list of (name, x, y)
    exit_direction: 'north', 'south', 'east', 'west'
    stub_spacing: mm between parallel lanes
    first_lane: mm coordinate of the innermost lane

    Returns list of (name, x, y, stub_end_x, stub_end_y, lane_index, label)
    sorted from inner (shortest stub) to outer (longest stub).
    """
    # Sort pins by distance to exit edge.
    # "nearest to exit" = inner = shortest stub.
    if exit_direction == 'south':
        # Largest y is nearest to south exit
        sorted_pins = sorted(pins, key=lambda p: -p[2])
    elif exit_direction == 'north':
        # Smallest y is nearest to north exit
        sorted_pins = sorted(pins, key=lambda p: p[2])
    elif exit_direction == 'east':
        # Largest x is nearest to east exit
        sorted_pins = sorted(pins, key=lambda p: -p[1])
    elif exit_direction == 'west':
        # Smallest x is nearest to west exit
        sorted_pins = sorted(pins, key=lambda p: p[1])
    else:
        raise ValueError(f"Unknown exit direction: {exit_direction}")

    # Stub direction is perpendicular to pin row, away from IC body.
    # For vertical pin rows (pins share x): stub goes east or west.
    # For horizontal pin rows (pins share y): stub goes north or south.
    # Detect from pin positions.
    xs = [p[1] for p in pins]
    ys = [p[2] for p in pins]
    x_spread = max(xs) - min(xs)
    y_spread = max(ys) - min(ys)

    if y_spread > x_spread:
        pin_row_axis = 'vertical'
    else:
        pin_row_axis = 'horizontal'

    results = []
    n = len(sorted_pins)
    for i, (name, px, py) in enumerate(sorted_pins):
        lane_offset = i  # 0 = inner (shortest), n-1 = outer (longest)
        label = 'inner' if i == 0 else ('outer' if i == n - 1 else f'mid-{i}')

        if pin_row_axis == 'vertical':
            # Stubs go in x direction. first_lane is the x of the innermost lane.
            # Determine stub direction from first_lane vs pin x.
            stub_x = first_lane - (lane_offset * stub_spacing) if first_lane < px else first_lane + (lane_offset * stub_spacing)
            # Correct: inner = first_lane, outer = further from pin row
            if first_lane < px:
                stub_x = first_lane - lane_offset * stub_spacing
            else:
                stub_x = first_lane + lane_offset * stub_spacing
            stub_y = py
        else:
            # Stubs go in y direction.
            stub_x = px
            if first_lane < py:
                stub_y = first_lane - lane_offset * stub_spacing
            else:
                stub_y = first_lane + lane_offset * stub_spacing

        results.append((name, px, py, stub_x, stub_y, lane_offset, label))

    return results


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)

    exit_dir = sys.argv[1]
    stub_spacing = float(sys.argv[2])
    first_lane = float(sys.argv[3])

    pins = []
    for arg in sys.argv[4:]:
        parts = arg.split(',')
        name = parts[0]
        x = float(parts[1])
        y = float(parts[2])
        pins.append((name, x, y))

    results = fanout_order(pins, exit_dir, stub_spacing, first_lane)

    print(f"Fan-out: {len(pins)} pins, exit={exit_dir}, spacing={stub_spacing}mm, first_lane={first_lane}mm")
    print(f"Pin row: {'vertical' if results else '?'}")
    print()
    for name, px, py, sx, sy, idx, label in results:
        stub_len = abs(sx - px) + abs(sy - py)
        print(f"  {idx+1}. {name:8s} ({px},{py}) -> stub ({sx},{sy})  len={stub_len:.1f}mm  [{label}]")
        print(f"     route: {name} {px},{py} {sx},{sy} F.Cu margin=3")
    print()
    print("Route stubs first (all), then trunks inner-to-outer.")


if __name__ == '__main__':
    main()
