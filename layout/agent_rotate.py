"""
agent_rotate.py — Rotate a component, export to KiCad, print ratline total.

Usage:
  python agent_rotate.py U2 90
  python agent_rotate.py U2 90 --ignore GNDREF,GND
"""

import sys
import glob
import json
import subprocess
import urllib.request
import kipy
from ratlines import mst_length, parse_ignore_nets

SERVER = "http://172.17.0.1:8081"


def main():
    ignore_nets = parse_ignore_nets()

    # Parse positional args (skip --ignore and its value)
    args = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--ignore":
            i += 2
            continue
        args.append(sys.argv[i])
        i += 1

    if len(args) != 2:
        print("Usage: python agent_rotate.py <component> <degrees> [--ignore NET1,NET2]")
        sys.exit(1)

    comp = args[0]
    deg = int(args[1])

    # 1. Rotate via layout server
    req = urllib.request.Request(
        SERVER,
        data=json.dumps({"rotate": {comp: deg}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    print(f"Rotated {comp} to {deg}° (v{result['v']})")

    # 2. Export to KiCad via export_kicad.py
    state_req = urllib.request.urlopen(SERVER)
    state = state_req.read()
    proc = subprocess.run(
        [sys.executable, "export_kicad.py"],
        input=state,
        capture_output=True,
    )
    print(proc.stdout.decode().strip())
    if proc.returncode != 0:
        print(proc.stderr.decode(), file=sys.stderr)
        sys.exit(1)

    # 3. Read ratlines from KiCad
    socks = glob.glob('/tmp/kicad/api-*.sock')
    if not socks:
        raise RuntimeError('No KiCad PCB editor socket found in /tmp/kicad/')
    kicad = kipy.KiCad(socket_path='ipc://' + socks[0])
    board = kicad.get_board()

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
    for net_name, pads in nets.items():
        if len(pads) < 2 or net_name in ignore_nets:
            continue
        length, _ = mst_length(pads)
        total += length

    print(f"\nRatline total: {total:.2f} mm")


if __name__ == "__main__":
    main()
