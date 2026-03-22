#!/usr/bin/env python3
"""Force-directed auto-placement using the dpcb viewer API.

Iteratively moves each component along its combined force + repulsion
vector by a small step. Components settle toward a local equilibrium
where attraction to connections balances repulsion from foreign corridors.

Fixed components (connectors) are pinned and do not move.

Usage:
    python3 utilities/script_autoplace_force.py [options]

Options:
    --iterations N    Number of full passes over all components (default: 50)
    --step F          Step size as fraction of vector magnitude (default: 0.3)
    --min-step F      Minimum move distance in mm (default: 0.1)
    --max-step F      Maximum move distance in mm (default: 2.0)
    --decay F         Step size multiplied by this each iteration (default: 0.95)
    --pinned REF,...  Comma-separated refs to pin in place (default: J1,J2,J3)
    --snap F          Snap to grid in mm, 0 to disable (default: 0.5)
    --save PATH       Save result to host path after placement
    --dry-run         Print moves without executing

Environment variables:
    DPCB_HOST  — viewer host (default: 172.17.0.1)
    DPCB_PORT  — viewer port (default: 9876)
"""
import argparse, math, os, re, socket, sys, time

HOST = os.environ.get('DPCB_HOST', '172.17.0.1')
PORT = int(os.environ.get('DPCB_PORT', '9876'))


def send_cmd(s, cmd):
    s.sendall((cmd + '\n').encode())
    buf = ''
    while True:
        buf += s.recv(4096).decode()
        if '\n.\n' in buf:
            return buf[:buf.index('\n.\n')]


def parse_vector_output(text):
    """Parse force, repulsion, or component_repulsion output into dict of ref -> (x, y, vx, vy, mag)."""
    results = {}
    for line in text.strip().split('\n'):
        # Match: "  R1   (8.5,12.5)  force=(1.2,3.4)  mag=5.6mm  ..."
        # or:    "  R1   (8.5,12.5)  repulsion=(1.2,3.4)  mag=5.6mm  ..."
        # or:    "  R1   (8.5,12.5)  r=1.1mm  push=(1.2,3.4)  mag=5.6mm"
        m = re.match(
            r'\s+(\S+)\s+\(([0-9.-]+),([0-9.-]+)\)\s+'
            r'(?:(?:force|repulsion)=\(([0-9.-]+),([0-9.-]+)\)|'
            r'r=[0-9.]+mm\s+push=\(([0-9.-]+),([0-9.-]+)\))\s+'
            r'mag=([0-9.]+)mm',
            line
        )
        if m:
            ref = m.group(1)
            x, y = float(m.group(2)), float(m.group(3))
            # Groups 4,5 for force/repulsion format; groups 6,7 for push format
            if m.group(4) is not None:
                vx, vy = float(m.group(4)), float(m.group(5))
            else:
                vx, vy = float(m.group(6)), float(m.group(7))
            mag = float(m.group(8))
            results[ref] = (x, y, vx, vy, mag)
    return results


def get_blockage_count(s):
    rat = send_cmd(s, 'check_ratsnest 2.0')
    return rat.count('BLOCKED')


def get_pad_crowding_count(s):
    pads = send_cmd(s, 'check_crowding_pads 1.5')
    return pads.count('CROWDED')


def get_positions_and_rotations(s):
    """Get component positions and rotations from individual force queries."""
    force_all = send_cmd(s, 'force')
    positions = {}
    for line in force_all.strip().split('\n'):
        m = re.match(r'\s+(\S+)\s+\(([0-9.-]+),([0-9.-]+)\)', line)
        if m:
            positions[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return positions


def snap_to_grid(val, grid):
    if grid <= 0:
        return val
    return round(val / grid) * grid


def main():
    parser = argparse.ArgumentParser(description='Force-directed auto-placement')
    parser.add_argument('--iterations', type=int, default=50)
    parser.add_argument('--step-size', type=float, default=0.5,
                        help='Fixed step size in mm (direction only, magnitude ignored)')
    parser.add_argument('--pinned', type=str, default='J1,J2,J3')
    parser.add_argument('--no-force', action='store_true',
                        help='Disable force attraction, use component_repulsion only (spreading)')
    parser.add_argument('--snap', type=float, default=0.5)
    parser.add_argument('--save', type=str, default='')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--delay', type=float, default=0.0,
                        help='Delay in seconds after each kept move (for visual feedback)')
    args = parser.parse_args()

    pinned = set(args.pinned.split(',')) if args.pinned else set()

    s = socket.create_connection((HOST, PORT), timeout=10)

    # Initial state
    blockages = get_blockage_count(s)
    pad_crowd = get_pad_crowding_count(s)
    print(f"Initial: blk={blockages}  padcr={pad_crowd}")
    print(f"Pinned:  {', '.join(sorted(pinned))}")
    mode = "component_repulsion only (spreading)" if args.no_force else "force (attraction) + component_repulsion (spacing)"
    print(f"Mode:    {mode}")
    print(f"Step:    {args.step_size}mm fixed  (snap={args.snap})")
    print()

    best_blockages = blockages
    moves_made = 0
    moves_kept = 0
    moves_undone = 0

    for iteration in range(args.iterations):
        # Get force (attraction) and component repulsion (physical spacing)
        force_data = {} if args.no_force else parse_vector_output(send_cmd(s, 'force'))
        comp_repul = parse_vector_output(send_cmd(s, 'component_repulsion'))

        moved_this_iter = 0
        iter_kept = 0

        # Use component_repulsion keys as the component list
        refs = sorted(comp_repul.keys())
        for ref in refs:
            if ref in pinned:
                continue

            if ref not in comp_repul:
                continue

            cur_x, cur_y = comp_repul[ref][0], comp_repul[ref][1]

            # Component repulsion vector (push apart)
            rx, ry = comp_repul[ref][2], comp_repul[ref][3]

            # Force attraction vector (pull together)
            fx, fy = 0.0, 0.0
            if not args.no_force and ref in force_data:
                fx, fy = force_data[ref][2], force_data[ref][3]

            # Combined: attraction + repulsion
            dx = fx + rx
            dy = fy + ry
            mag = math.sqrt(dx * dx + dy * dy)

            if mag < 0.01:
                continue  # No meaningful movement

            # Fixed step in the direction of the combined vector
            move_dist = args.step_size

            # Normalize and apply
            nx = dx / mag * move_dist
            ny = dy / mag * move_dist

            new_x = cur_x + nx
            new_y = cur_y + ny

            # Snap to grid
            if args.snap > 0:
                new_x = snap_to_grid(new_x, args.snap)
                new_y = snap_to_grid(new_y, args.snap)

            # Skip if snapped position is same as current
            if abs(new_x - cur_x) < 0.05 and abs(new_y - cur_y) < 0.05:
                continue

            # Board bounds check (keep components on or near board)
            # Allow some margin for off-board components
            new_x = max(-5.0, min(35.0, new_x))
            new_y = max(-5.0, min(25.0, new_y))

            moves_made += 1

            if args.dry_run:
                print(f"  {ref:4s} ({cur_x:.1f},{cur_y:.1f}) -> ({new_x:.1f},{new_y:.1f})  "
                      f"F=({fx:.1f},{fy:.1f}) R=({rx:.1f},{ry:.1f})")
                moved_this_iter += 1
                continue

            # Execute move — always accept
            result = send_cmd(s, f'move {ref} {new_x:.1f},{new_y:.1f}')
            if not result.startswith('OK'):
                continue

            moves_kept += 1
            iter_kept += 1
            moved_this_iter += 1
            if args.delay > 0:
                time.sleep(args.delay)

        # Report at end of iteration
        blockages = get_blockage_count(s)
        pad_crowd = get_pad_crowding_count(s)
        if blockages < best_blockages:
            best_blockages = blockages

        if not args.dry_run:
            print(f"  iter {iteration+1:3d}:  blk={blockages}  padcr={pad_crowd}  "
                  f"moved={moved_this_iter}")
        else:
            print(f"  iter {iteration+1:3d}:  (dry run)  moves={moved_this_iter}")

        # Early exit if no moves made
        if moved_this_iter == 0:
            print(f"  Converged — no moves possible at step={args.step_size}mm")
            break

    print()
    print(f"Done: {args.iterations} iterations  {moves_made} tried  "
          f"{moves_kept} kept  {moves_undone} undone")
    print(f"Final: blk={blockages}  padcr={pad_crowd}  (best blk={best_blockages})")

    # Print final positions
    print()
    print("Final positions:")
    positions = get_positions_and_rotations(s)
    for ref in sorted(positions):
        pin = " (pinned)" if ref in pinned else ""
        print(f"  {ref:4s} ({positions[ref][0]:.1f}, {positions[ref][1]:.1f}){pin}")

    if args.save:
        result = send_cmd(s, f'save {args.save}')
        print(f"\n{result}")

    s.close()


if __name__ == '__main__':
    main()
