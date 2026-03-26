"""
ratlines.py — Extract ratline distances from KiCad

Connects to a running KiCad instance, reads all pads,
groups them by net, and computes the minimum spanning tree
(MST) distance for each net. Prints per-net ratlines and
a total ratline length.

Usage:
  python ratlines.py [--ignore NET1,NET2,...]
  python ratlines.py --ignore GNDREF,GND
  python ratlines.py                          # no nets ignored
"""

import sys
import glob
import math
import kipy

# Default: no nets ignored. Pass --ignore to exclude nets.
IGNORE_NETS = set()


def parse_ignore_nets(args=None):
    """Parse --ignore flag from args list. Returns set of net names to ignore."""
    if args is None:
        args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--ignore" and i + 1 < len(args):
            return {n.strip() for n in args[i + 1].split(",")}
    return set()


def mst_length(pads):
    """Prim's MST on pad positions. Returns total edge length in mm and edge list."""
    if len(pads) < 2:
        return 0.0, []

    n = len(pads)
    in_tree = [False] * n
    min_cost = [float('inf')] * n
    min_from = [-1] * n

    in_tree[0] = True
    for j in range(1, n):
        dx = pads[j][1] - pads[0][1]
        dy = pads[j][2] - pads[0][2]
        min_cost[j] = math.hypot(dx, dy)
        min_from[j] = 0

    total = 0.0
    edges = []
    for _ in range(n - 1):
        u = -1
        best = float('inf')
        for j in range(n):
            if not in_tree[j] and min_cost[j] < best:
                best = min_cost[j]
                u = j
        if u == -1:
            break
        in_tree[u] = True
        total += best
        edges.append((pads[min_from[u]], pads[u], best))

        for j in range(n):
            if not in_tree[j]:
                dx = pads[j][1] - pads[u][1]
                dy = pads[j][2] - pads[u][2]
                d = math.hypot(dx, dy)
                if d < min_cost[j]:
                    min_cost[j] = d
                    min_from[j] = u

    return total, edges


def main():
    global IGNORE_NETS
    IGNORE_NETS = parse_ignore_nets()

    socks = glob.glob('/tmp/kicad/api-*.sock')
    if not socks:
        raise RuntimeError('No KiCad PCB editor socket found in /tmp/kicad/')
    kicad = kipy.KiCad(socket_path='ipc://' + socks[0])
    board = kicad.get_board()

    # Build pad ID -> ref lookup via footprint definitions
    pad_id_to_ref = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value
        for p in fp.definition.pads:
            pad_id_to_ref[p.id.value] = ref

    # Gather pads grouped by net
    nets = {}  # net_name -> [(label, x_mm, y_mm), ...]
    for pad in board.get_pads():
        net_name = pad.net.name if pad.net else ""
        if not net_name or net_name == "":
            continue
        x_mm = pad.position.x / 1e6
        y_mm = pad.position.y / 1e6
        ref = pad_id_to_ref.get(pad.id.value, "?")
        label = f"{ref}:{pad.number}"
        nets.setdefault(net_name, []).append((label, x_mm, y_mm))

    # Compute MST for each net, sort by longest first
    results = []
    grand_total = 0.0
    ignored_total = 0.0
    for net_name, pads in nets.items():
        if len(pads) < 2:
            continue
        length, edges = mst_length(pads)
        results.append((length, net_name, pads, edges))
        if net_name in IGNORE_NETS:
            ignored_total += length
        else:
            grand_total += length

    results.sort(reverse=True)

    # Per-component pull vectors: sum of (direction * distance) for all ratline edges
    pulls = {}  # ref -> (sum_dx, sum_dy, total_dist)
    for length, net_name, pads, edges in results:
        for (p1, p2, d) in edges:
            ref1 = p1[0].split(":")[0]
            ref2 = p2[0].split(":")[0]
            dx = p2[1] - p1[1]
            dy = p2[2] - p1[2]
            # p1 is pulled toward p2, p2 is pulled toward p1
            px, py, pt = pulls.get(ref1, (0, 0, 0))
            pulls[ref1] = (px + dx, py + dy, pt + d)
            px, py, pt = pulls.get(ref2, (0, 0, 0))
            pulls[ref2] = (px - dx, py - dy, pt + d)

    # Print
    for length, net_name, pads, edges in results:
        print(f"{net_name:30s}  {len(pads):2d} pads  {length:8.2f} mm")
        for (p1, p2, d) in edges:
            dx = p2[1] - p1[1]
            dy = p2[2] - p1[2]
            arrow = ""
            if abs(dx) > abs(dy):
                arrow = "→" if dx > 0 else "←"
            else:
                arrow = "↓" if dy > 0 else "↑"
            print(f"    {p1[0]:>10s} -> {p2[0]:<10s}  {d:6.2f} mm  {arrow} (dx={dx:+.1f} dy={dy:+.1f})")

    print(f"\n{'TOTAL (signal)':30s}  {grand_total:8.2f} mm")
    if ignored_total:
        print(f"{'TOTAL (ignored GND)':30s}  {ignored_total:8.2f} mm")
        print(f"{'TOTAL (all)':30s}  {grand_total + ignored_total:8.2f} mm")

    # Print component pull summary — which direction each component wants to move
    print(f"\n{'--- Pull vectors (move hints) ---':30s}")
    pull_list = [(math.hypot(dx, dy), ref, dx, dy, td)
                 for ref, (dx, dy, td) in pulls.items()]
    pull_list.sort(reverse=True)
    for mag, ref, dx, dy, td in pull_list:
        if mag < 1.0:
            continue
        if abs(dx) > abs(dy):
            hint = f"{'right' if dx > 0 else 'left':>5s}"
        else:
            hint = f"{'down' if dy > 0 else 'up':>5s}"
        print(f"  {ref:6s}  pull {hint} {mag:5.1f} mm  (dx={dx:+.1f} dy={dy:+.1f})  ratline={td:.1f}mm")


if __name__ == '__main__':
    main()
