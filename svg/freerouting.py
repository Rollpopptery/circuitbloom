#!/usr/bin/env python3
"""
freerouting.py — FreeRouting autorouter handler for the SVG server.

Generates a DSN file from the current board, runs the FreeRouting jar,
imports the SES result back into the board overlays, and updates server_state.

Usage:
    from freerouting import run_freerouting_handler, FREEROUTING_JAR
    code, ct, body = run_freerouting_handler(params)
"""

import json
import os
import subprocess
import tempfile

import server_state as ss
from svg_to_dsn import board_to_dsn
from dsn_to_svg import ses_to_svg

FREEROUTING_JAR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'freerouting.jar')


def run_freerouting_handler(params: dict) -> tuple[int, str, bytes]:
    """
    Run the FreeRouting autorouter on the current board.

    Reads routing parameters from params dict (query string format):
        clearance    float mm  default 0.2
        track_width  float mm  default 0.25
        via_od       float mm  default 0.6
        via_drill    float mm  default 0.3

    Returns:
        (status_code, content_type, body)
    """
    if not os.path.exists(FREEROUTING_JAR):
        return 500, 'application/json', json.dumps({
            "ok": False,
            "error": f"freerouting.jar not found at {FREEROUTING_JAR}"
        }).encode()

    with ss.board_lock:
        if ss.current_board is None:
            return 404, 'application/json', \
                b'{"ok":false,"error":"no board loaded"}'

        clearance_mm   = float(params.get('clearance',   ['0.2'])[0])
        track_mm       = float(params.get('track_width', ['0.25'])[0])
        via_od_mm      = float(params.get('via_od',      ['0.6'])[0])
        via_drill_mm   = float(params.get('via_drill',   ['0.3'])[0])
        timeout_s      = int(params.get('timeout',       ['300'])[0])

        dsn = board_to_dsn(ss.current_board,
                           clearance_mm=clearance_mm,
                           track_width_mm=track_mm,
                           via_od_mm=via_od_mm,
                           via_drill_mm=via_drill_mm)

    # Write DSN to temp file
    with tempfile.NamedTemporaryFile(suffix='.dsn', mode='w', delete=False) as f:
        f.write(dsn)
        dsn_path = f.name
    ses_path = dsn_path.replace('.dsn', '.ses')

    # Run FreeRouting
    try:
        subprocess.run(
            ['java', '-jar', FREEROUTING_JAR,
             '-de', dsn_path, '-do', ses_path],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 500, 'application/json', \
            b'{"ok":false,"error":"freerouting timed out"}'
    except Exception as e:
        return 500, 'application/json', \
            json.dumps({"ok": False, "error": str(e)}).encode()
    finally:
        if os.path.exists(dsn_path):
            os.unlink(dsn_path)

    if not os.path.exists(ses_path):
        return 500, 'application/json', \
            b'{"ok":false,"error":"no SES output produced"}'

    with open(ses_path, 'r') as f:
        ses_text = f.read()
    os.unlink(ses_path)

    with ss.board_lock:
        count = ses_to_svg(ses_text, ss.current_board)

    print(f"  routed: {count} segments, clearance={clearance_mm}mm, "
          f"track={track_mm}mm, via_od={via_od_mm}mm")

    return 200, 'application/json', json.dumps({
        "ok":             True,
        "segments":       count,
        "clearance_mm":   clearance_mm,
        "track_width_mm": track_mm,
        "via_od_mm":      via_od_mm,
        "via_drill_mm":   via_drill_mm,
    }).encode()