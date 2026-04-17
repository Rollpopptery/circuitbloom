#!/usr/bin/env python3
"""
collect_trace_patterns.py — Open each .kicad_pcb in a directory,
extract trace patterns, and store in ChromaDB with DINOv2 embeddings.

Usage:
    python3 collect_trace_patterns.py /path/to/kicad_boards
    python3 collect_trace_patterns.py /path/to/kicad_boards --db ./my_collection
    python3 collect_trace_patterns.py /path/to/kicad_boards --db ./my_collection --append
"""

import json
import os
import sys
import glob
import urllib.request
import argparse
from pcb_check import is_two_layer


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from trace_patterndb import index_trace_patterns
from db_utils import board_exists, open_collection

SERVER = "http://localhost:8084"


def open_board(path):
    """Open a board in KiCad via the route server."""
    data = json.dumps({"action": "open_board", "path": path}).encode()
    req = urllib.request.Request(SERVER + "/", data=data,
                                headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return resp.get("ok", False), resp.get("message", "")
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Collect trace patterns from KiCad boards")
    parser.add_argument("directory",
                        help="Absolute path to folder with .kicad_pcb files")
    parser.add_argument("--db", default=None,
                        help="ChromaDB persist directory (default: ./trace_pattern_collection)")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing collection (don't clear on first board)")
    args = parser.parse_args()

    board_dir = os.path.abspath(args.directory)
    files = sorted(glob.glob(os.path.join(board_dir, "*.kicad_pcb")))

    if not files:
        print(f"No .kicad_pcb files found in {board_dir}")
        sys.exit(1)

    print(f"Found {len(files)} boards in {board_dir}")
    if args.append:
        print("Mode: APPEND to existing collection")
    else:
        print("Mode: FRESH collection (first board clears existing)")
    print()

    # Open existing collection for board_exists checks (append mode only)
    existing_collection = None
    if args.append and args.db:
        try:
            existing_collection = open_collection(args.db)
            print(f"Existing collection: {existing_collection.count()} patterns")
        except Exception:
            existing_collection = None

    success = 0
    failed = 0
    skipped = 0
    total_patterns = 0

    for i, filepath in enumerate(files):
        name = os.path.splitext(os.path.basename(filepath))[0]
        abspath = os.path.abspath(filepath)

        print(f"[{i+1}/{len(files)}] {name}")

       
        if not is_two_layer(abspath):
            print(f"  [skip] not a 2-layer board")
            skipped += 1
            continue

        # Skip if board already exists in collection (append mode only)
        if args.append and existing_collection is not None:
            if board_exists(existing_collection, name):
                print(f"  [skip] already in collection")
                skipped += 1
                continue

        print(f"  Opening: {abspath}")

        ok, msg = open_board(abspath)
        if not ok:
            print(f"  FAILED to open: {msg}")
            failed += 1
            continue

        print(f"  {msg}")

        try:
            collection = index_trace_patterns(
                append=(args.append or i > 0 or success > 0),
                board_name=name,
                persist_dir=args.db,
            )
            # Update reference to collection after first index
            if existing_collection is None:
                existing_collection = collection
            else:
                existing_collection = collection
            total_patterns = collection.count()
            success += 1
        except Exception as e:
            print(f"  FAILED to index: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

        print()

    print(f"{'=' * 60}")
    print(f"Done: {success} boards indexed, {skipped} skipped, {failed} failed")
    print(f"Total patterns in collection: {total_patterns}")


if __name__ == "__main__":
    main()