#!/usr/bin/env python3
"""
reload_board.py — Tell KiCad to reload the current board from disk.

Run this after gen_pcb.py to see changes live in KiCad.

Usage:
    python3 reload_board.py

Requirements:
    - KiCad 9 running with IPC API enabled (Preferences > Plugins > Enable IPC API)
    - kicad-python installed: pip install kicad-python --break-system-packages
"""

import sys

def reload_board():
    try:
        from kipy import KiCad
    except ImportError:
        print("ERROR: kicad-python not installed.")
        print("       pip install kicad-python --break-system-packages")
        sys.exit(1)

    try:
        kicad = KiCad()
    except Exception as e:
        print(f"ERROR: Could not connect to KiCad IPC API: {e}")
        print("       Is KiCad running with IPC API enabled?")
        print("       Preferences > Plugins > Enable IPC API")
        sys.exit(1)

    try:
        board = kicad.get_board()
        name = board.name
        board.revert()
        print(f"Reloaded: {name}")
    except Exception as e:
        print(f"ERROR: Could not reload board: {e}")
        sys.exit(1)

if __name__ == "__main__":
    reload_board()