"""
try_rotations.py — Try all rotations for a component and report ratline totals.

Usage:
  python try_rotations.py U2
  python try_rotations.py U2 U1 J1
"""

import sys
import json
import math
import urllib.request
import kipy

SERVER = "http://172.17.0.1:8081"


def post(data):
    req = urllib.request.Request(
        SERVER,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_ratline_total(board):
    pad_id_to_ref = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value
        for p in fp.definition.pads:
            pad_id_to_ref[p.id.value] = ref

    nets = {}
    for pad in board.get_pads():
        net_name = pad.net.name if pad.net else ""
        if not net_name:
            continue
        x = pad.position.x / 1e6
        y = pad.position.y / 1e6
        ref = pad_id_to_ref.get(pad.id.value, "?")
        nets.setdefault(net_name, []).append((f"{ref}:{pad.number}", x, y))

    total = 0.0
    for pads in nets.values():
        if len(pads) < 2:
            continue
        total += mst_length(pads)
    return total


def mst_length(pads):
    n = len(pads)
    in_tree = [False] * n
    min_cost = [float('inf')] * n
    in_tree[0] = True
    for j in range(1, n):
        dx = pads[j][1] - pads[0][1]
        dy = pads[j][2] - pads[0][2]
        min_cost[j] = math.hypot(dx, dy)
    total = 0.0
    for _ in range(n - 1):
        u = min((j for j in range(n) if not in_tree[j]), key=lambda j: min_cost[j], default=-1)
        if u == -1:
            break
        in_tree[u] = True
        total += min_cost[u]
        for j in range(n):
            if not in_tree[j]:
                d = math.hypot(pads[j][1] - pads[u][1], pads[j][2] - pads[u][2])
                if d < min_cost[j]:
                    min_cost[j] = d
    return total


def main():
    components = sys.argv[1:]
    if not components:
        print("Usage: python try_rotations.py U2 [U1 ...]")
        sys.exit(1)

    kicad = kipy.KiCad()
    board = kicad.get_board()

    # Get current state
    with urllib.request.urlopen(SERVER) as resp:
        state = json.loads(resp.read())
    comp_table = state.get("components", {})

    for comp in components:
        current_rot = comp_table.get(comp, {}).get("rotation", 0)
        print(f"\n{comp} (current rotation: {current_rot}°)")
        print("-" * 40)

        results = []
        for deg in [0, 90, 180, 270]:
            post({"rotate": {comp: deg}})
            # Export to KiCad
            req = urllib.request.Request(f"http://172.17.0.1:8080/export", method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    resp.read()
            except Exception:
                pass
            # Re-read board state
            import time
            time.sleep(0.2)
            total = get_ratline_total(board)
            marker = " <-- current" if deg == current_rot else ""
            results.append((total, deg))
            print(f"  {deg:3d}°  {total:8.2f} mm{marker}")

        best_total, best_deg = min(results)
        print(f"  Best: {best_deg}° ({best_total:.2f} mm)")

        # Restore to best
        post({"rotate": {comp: best_deg}})
        print(f"  Set to {best_deg}°")


if __name__ == "__main__":
    main()
