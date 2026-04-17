#!/usr/bin/env python3
"""
server_state.py — Shared mutable state for the Circuit Bloom 2 SVG server.

All modules that need to read or write the current board or corridors
import from here. The lock must be held when reading or writing.

Usage:
    import server_state as ss

    with ss.board_lock:
        board = ss.current_board
        ss.current_board = new_board
"""

import threading
from board_svg import BoardSVG
from corridor_map import CorridorMap

# ── Shared state ──────────────────────────────────────────────────────────────

current_board:     BoardSVG    = None
current_corridors: CorridorMap = None
board_lock = threading.Lock()