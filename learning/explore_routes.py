#!/usr/bin/env python3
"""
explore_routes.py — Rebuild routes from the loaded board, print details
with component info for pad endpoints, and highlight each net in the viewer.

Assumes route server (port 8084) and KiCad are running.
"""

import json
import sys
import os
import urllib.request

# Import router modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from rebuild_routes import rebuild_routes
from component_info import ComponentInfo
from grab_layer import find_socket

SERVER = "http://localhost:8084"


def highlight(net, color="#ffff00"):
    data = json.dumps({"action": "highlight", "net": net, "color": color}).encode()
    req = urllib.request.Request(SERVER + "/", data=data,
                                headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)


def get_state():
    with urllib.request.urlopen(SERVER + "/") as r:
        return json.loads(r.read())


def pad_label(pad_info, comp_info):
    """Format a pad endpoint with component details."""
    if not pad_info:
        return None
    ref = pad_info["ref"]
    pin = pad_info["pin"]
    comp = comp_info.component(ref) if comp_info else None
    if comp:
        fp = str(comp['footprint']).split(':')[-1]
        return f"{ref}.{pin} ({comp['value']}, {fp})"
    return f"{ref}.{pin}"


def main():
    # Get board state from route server
    print("Fetching board state...")
    state = get_state()
    tracks = state.get("tracks", [])
    pads = state.get("pads", [])
    vias = state.get("vias", [])
    print(f"  {len(tracks)} tracks, {len(pads)} pads, {len(vias)} vias")

    # Connect to KiCad for component info
    print("Connecting to KiCad for component info...")
    sock = find_socket()
    comp_info = None
    if sock:
        try:
            comp_info = ComponentInfo(sock)
            print("  OK")
        except Exception as e:
            print(f"  Warning: could not get component info: {e}")
    else:
        print("  Warning: no KiCad socket found, component info unavailable")

    # Rebuild routes
    print("Rebuilding routes...")
    routes = rebuild_routes(tracks, pads, vias)
    print(f"  {len(routes)} routes reconstructed\n")

    # Group by net for highlighting
    current_net = None
    routes_sorted = sorted(routes, key=lambda r: (r["net"], r["type"]))

    for r in routes_sorted:
        net = r["net"]

        # Highlight net when it changes
        if net != current_net:
            highlight(net)
            current_net = net
            print(f"{'=' * 60}")
            print(f"NET: {net}")
            print(f"{'=' * 60}")

        # Route summary
        via_str = f"  {len(r['vias'])} via(s)" if r["vias"] else ""
        layers = ",".join(r["layers"])
        print(f"\n  [{r['type']}]  {r['length_mm']:.1f}mm  {layers}{via_str}")

        # From endpoint
        from_label = pad_label(r["from_pad"], comp_info)
        if from_label:
            print(f"    FROM: {from_label}")
        else:
            print(f"    FROM: ({r['from_pt'][0]:.2f}, {r['from_pt'][1]:.2f})  [trace junction]")

        # To endpoint
        to_label = pad_label(r["to_pad"], comp_info)
        if to_label:
            print(f"    TO:   {to_label}")
        else:
            print(f"    TO:   ({r['to_pt'][0]:.2f}, {r['to_pt'][1]:.2f})  [trace junction]")

        # Print pad net context for pin endpoints
        if r["from_pad"] and comp_info:
            p = comp_info.pad(r["from_pad"]["ref"], r["from_pad"]["pin"])
            if p and p.get("net"):
                print(f"           net on pad: {p['net']}")
        if r["to_pad"] and comp_info:
            p = comp_info.pad(r["to_pad"]["ref"], r["to_pad"]["pin"])
            if p and p.get("net"):
                print(f"           net on pad: {p['net']}")

    # Clear highlight
    highlight("")
    print(f"\n{'=' * 60}")
    print(f"Total: {len(routes)} routes across {len(set(r['net'] for r in routes))} nets")


if __name__ == "__main__":
    main()
