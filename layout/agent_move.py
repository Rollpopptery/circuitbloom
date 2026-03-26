"""
agent_move.py — Move a component to a different group, export to KiCad, print ratline total.

Usage:
  python agent_move.py R3 interface                     # move R3 into group "interface"
  python agent_move.py R3 interface 2                   # move R3 into "interface" at index 2
  python agent_move.py R3 interface after:D2            # move R3 after D2 in "interface"
  python agent_move.py R3 interface before:D2           # move R3 before D2 in "interface"
  python agent_move.py R3 interface after:D2 --ignore GNDREF,GND
"""

import sys
import glob
import json
import subprocess
import urllib.request
import kipy
from ratlines import mst_length, parse_ignore_nets

SERVER = "http://172.17.0.1:8081"


def find_and_remove(node, comp_id):
    """Find and remove a node by id. Returns (removed_node, success)."""
    if "children" not in node:
        return None, False
    for i, child in enumerate(node["children"]):
        if child["id"] == comp_id:
            return node["children"].pop(i), True
        removed, ok = find_and_remove(child, comp_id)
        if ok:
            return removed, True
    return None, False


def find_group(node, group_id):
    """Find a group node by id."""
    if node["id"] == group_id:
        return node
    for child in node.get("children", []):
        found = find_group(child, group_id)
        if found is not None:
            return found
    return None


def insert_at(group, comp_node, position):
    """Insert comp_node into group's children at the given position.

    position can be:
      - an int index
      - "after:ID" — insert after sibling with that id
      - "before:ID" — insert before sibling with that id
      - None — append at end
    """
    children = group.setdefault("children", [])
    if position is None:
        children.append(comp_node)
    elif isinstance(position, int):
        children.insert(position, comp_node)
    elif isinstance(position, str) and position.startswith("after:"):
        target = position[6:]
        for i, child in enumerate(children):
            if child["id"] == target:
                children.insert(i + 1, comp_node)
                return
        children.append(comp_node)  # fallback: append
    elif isinstance(position, str) and position.startswith("before:"):
        target = position[7:]
        for i, child in enumerate(children):
            if child["id"] == target:
                children.insert(i, comp_node)
                return
        children.insert(0, comp_node)  # fallback: prepend
    else:
        children.append(comp_node)


def print_tree(node, indent=0):
    """Print tree structure compactly."""
    prefix = "  " * indent
    if "children" in node:
        arr = node["arrange"]
        ids = [c["id"] for c in node["children"]]
        print(f"{prefix}{node['id']} ({arr}): [{', '.join(ids)}]")
        for child in node["children"]:
            if "children" in child:
                print_tree(child, indent + 1)
    else:
        print(f"{prefix}{node['id']} ({node['w']}x{node['h']})")


def get_ratline_total(ignore_nets):
    """Connect to KiCad and compute total MST ratline length."""
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
    return total


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

    if len(args) < 2:
        print("Usage: python agent_move.py <component> <target_group> [index|after:ID|before:ID] [--ignore NET1,NET2]")
        sys.exit(1)

    comp_id = args[0]
    target_group_id = args[1]
    position = None
    if len(args) >= 3:
        pos_arg = args[2]
        if pos_arg.startswith("after:") or pos_arg.startswith("before:"):
            position = pos_arg
        else:
            position = int(pos_arg)

    # 1. Get current tree
    with urllib.request.urlopen(SERVER + "/tree") as resp:
        tree = json.loads(resp.read())

    # 2. Remove component from current location
    comp_node, ok = find_and_remove(tree, comp_id)
    if not ok:
        print(f"ERROR: {comp_id} not found in tree")
        sys.exit(1)

    # 3. Find target group and insert
    target = find_group(tree, target_group_id)
    if target is None:
        print(f"ERROR: group {target_group_id} not found in tree")
        sys.exit(1)
    if "children" not in target:
        print(f"ERROR: {target_group_id} is a leaf, not a group")
        sys.exit(1)

    insert_at(target, comp_node, position)

    # 4. POST updated tree
    req = urllib.request.Request(
        SERVER,
        data=json.dumps({"tree": tree}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    print(f"Moved {comp_id} to {target_group_id} (v{result['v']})")

    # 5. Print new tree structure
    print_tree(tree)

    # 6. Export to KiCad
    with urllib.request.urlopen(SERVER) as resp:
        state = resp.read()
    proc = subprocess.run(
        [sys.executable, "export_kicad.py"],
        input=state,
        capture_output=True,
    )
    print(proc.stdout.decode().strip())
    if proc.returncode != 0:
        print(proc.stderr.decode(), file=sys.stderr)
        sys.exit(1)

    # 7. Read ratlines
    total = get_ratline_total(ignore_nets)
    print(f"\nRatline total: {total:.2f} mm")


if __name__ == "__main__":
    main()
