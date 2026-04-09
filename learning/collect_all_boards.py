#!/usr/bin/env python3
"""
collect_all_boards.py — Open each .kicad_pcb in a directory, rebuild routes,
and collect them all into a single ChromaDB collection.

Usage:
    python3 collect_all_boards.py [directory] [--host-prefix PATH]

    directory:     path to folder with .kicad_pcb files (default: ../../kicad_examples)
    --host-prefix: host filesystem prefix for open_board (default: /home/ric/projects/pcb_design)

The script uses the route server's open_board endpoint, which requires host
filesystem paths. The host prefix is prepended to the relative board path.
"""

import json
import os
import sys
import time
import glob
import urllib.request
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from route_vectordb import index_routes

SERVER = "http://localhost:8084"
DEFAULT_HOST_PREFIX = os.environ.get("KICAD_HOST_PREFIX", "")


def open_board(host_path):
    """Open a board in KiCad via the route server."""
    data = json.dumps({"action": "open_board", "path": host_path}).encode()
    req = urllib.request.Request(SERVER + "/", data=data,
                                headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return resp.get("ok", False), resp.get("message", "")
    except Exception as e:
        return False, str(e)


def get_board_files(directory):
    """List all .kicad_pcb files in a directory, sorted by name."""
    pattern = os.path.join(directory, "*.kicad_pcb")
    files = sorted(glob.glob(pattern))
    return files


def board_name_from_path(path):
    """Extract board name from filename (without extension)."""
    return os.path.splitext(os.path.basename(path))[0]


def main():
    parser = argparse.ArgumentParser(description="Collect routes from all KiCad boards")
    parser.add_argument("directory", nargs="?",
                        default=os.path.join(os.path.dirname(__file__), "..", "..", "kicad_examples"))
    parser.add_argument("--host-prefix", default=DEFAULT_HOST_PREFIX,
                        help="Host filesystem prefix (or set KICAD_HOST_PREFIX env var)")
    args = parser.parse_args()

    if not args.host_prefix:
        print("Error: host prefix required. Either:")
        print("  export KICAD_HOST_PREFIX=/home/you/projects/pcb_design")
        print("  python3 collect_all_boards.py --host-prefix /home/you/projects/pcb_design")
        sys.exit(1)

    board_dir = os.path.abspath(args.directory)
    files = get_board_files(board_dir)

    if not files:
        print(f"No .kicad_pcb files found in {board_dir}")
        sys.exit(1)

    print(f"Found {len(files)} boards in {board_dir}")
    print(f"Host prefix: {args.host_prefix}")
    print()

    # Work out the relative path from workspace root to board directory
    # so we can construct host paths
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    success = 0
    failed = 0
    total_routes = 0

    for i, filepath in enumerate(files):
        name = board_name_from_path(filepath)
        rel_path = os.path.relpath(filepath, workspace_root)
        host_path = os.path.join(args.host_prefix, rel_path)

        print(f"[{i+1}/{len(files)}] {name}")
        print(f"  Opening: {host_path}")

        ok, msg = open_board(host_path)
        if not ok:
            print(f"  FAILED to open: {msg}")
            failed += 1
            continue

        print(f"  {msg}")

        try:
            # First board replaces, rest append
            collection = index_routes(
                append=(i > 0 or success > 0),
                board_name=name,
            )
            total_routes = collection.count()
            success += 1
        except Exception as e:
            print(f"  FAILED to index: {e}")
            failed += 1

        print()

    print(f"{'=' * 60}")
    print(f"Done: {success} boards indexed, {failed} failed")
    print(f"Total routes in collection: {total_routes}")


if __name__ == "__main__":
    main()
