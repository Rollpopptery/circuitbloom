"""Shared state, bloom loading/saving, and state-query helpers."""

import json
import math
import os
import sys
import threading
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'layout'))

from bloom_grid import load_bloom, build_grid, get_pad_positions, get_net_map, get_component_centres
from tree_to_xy import transform as resolve_positions, get_rects
from route_convert import net_color

# ============================================================
# STATE
# ============================================================

state = {
    "version": 0,
    "pads": [],
    "tracks": [],
    "vias": [],
    "board": [55, 45],
    "nets": {},
    "components": {},
    "rects": [],
    "highlight": None,
    "markers": [],
}

bloom_data = None
bloom_path = None
grid = None
lock = threading.Lock()


# ============================================================
# BLOOM LOAD / SAVE
# ============================================================

def _get_layout(bd):
    """Get the placement or layout_tree from bloom data."""
    return bd.get("placement") or bd.get("layout_tree")


def reload_bloom():
    """Load/reload bloom file into state and rebuild grid."""
    global bloom_data, grid

    if not bloom_path:
        return

    bloom_data = load_bloom(bloom_path)
    g, pad_positions, net_map = build_grid(bloom_data)
    grid = g

    # Build pad list for viewer
    pads = []
    components = bloom_data.get("components", {})
    for ref, pins in pad_positions.items():
        comp = components.get(ref, {})
        for pin_str, (x, y, smd) in pins.items():
            net = comp.get("pins", {}).get(pin_str, {}).get("net", "")
            pin_name = comp.get("pins", {}).get(pin_str, {}).get("name", pin_str)
            pads.append({
                "ref": ref, "pin": pin_str, "name": pin_name,
                "net": net, "x": round(x, 3), "y": round(y, 3),
                "smd": smd
            })

    # Build tracks/vias from bloom
    tracks = bloom_data.get("pcb", {}).get("tracks", [])
    vias = bloom_data.get("pcb", {}).get("vias", [])

    # Build net color map
    nets = {}
    for net_name in net_map:
        nets[net_name] = net_color(net_name)

    # Resolve positions and rects
    centres = get_component_centres(bloom_data)
    layout = _get_layout(bloom_data)
    rects = get_rects(layout) if layout else []

    board_dims = bloom_data.get("pcb", {}).get("board", [55, 45])
    rules = bloom_data.get("pcb", {}).get("rules", {})

    with lock:
        state["pads"] = pads
        state["tracks"] = tracks
        state["vias"] = vias
        state["board"] = {"width": board_dims[0], "height": board_dims[1], "rules": rules}
        state["nets"] = nets
        state["components"] = {ref: {"x": round(x, 3), "y": round(y, 3)}
                               for ref, (x, y) in centres.items()}
        state["rects"] = rects
        state["version"] += 1

    print(f"  Loaded: {len(pads)} pads, {len(tracks)} tracks, {len(vias)} vias, {len(nets)} nets")


def save_bloom():
    """Save tracks and vias back to bloom file."""
    if not bloom_path or not bloom_data:
        return False, "no bloom file loaded"

    with lock:
        bloom_data["pcb"]["tracks"] = state["tracks"]
        bloom_data["pcb"]["vias"] = state["vias"]

    with open(bloom_path, "w") as f:
        json.dump(bloom_data, f, indent=2)

    print(f"  Saved to {bloom_path}")
    return True, f"saved to {bloom_path}"


# ============================================================
# STATE HELPERS
# ============================================================

def snap_to_pad(x, y, net, tol=0.3):
    """Snap coordinates to the nearest pad on the given net within tolerance."""
    best_d = tol
    best_x, best_y = x, y
    for p in state["pads"]:
        if p["net"] != net:
            continue
        d = math.hypot(p["x"] - x, p["y"] - y)
        if d < best_d:
            best_d = d
            best_x, best_y = p["x"], p["y"]
    return best_x, best_y


def get_transitions(tol=0.15):
    """Find layer transitions — points where F.Cu and B.Cu tracks share an endpoint."""
    endpoints = defaultdict(lambda: defaultdict(set))
    with lock:
        tracks = state["tracks"]
        vias = state["vias"]

    for t in tracks:
        net = t["net"]
        layer = t["layer"]
        endpoints[net][layer].add((round(t["x1"] / tol) * tol, round(t["y1"] / tol) * tol))
        endpoints[net][layer].add((round(t["x2"] / tol) * tol, round(t["y2"] / tol) * tol))

    via_pos = set()
    for v in vias:
        via_pos.add((round(v["x"] / tol) * tol, round(v["y"] / tol) * tol))

    results = []
    for net, layers in endpoints.items():
        if "F.Cu" not in layers or "B.Cu" not in layers:
            continue
        shared = layers["F.Cu"] & layers["B.Cu"]
        for pt in shared:
            has_via = pt in via_pos
            results.append({
                "x": pt[0], "y": pt[1], "net": net,
                "status": "VIA" if has_via else "MISSING"
            })

    return {"transitions": results,
            "total": len(results),
            "missing": sum(1 for r in results if r["status"] == "MISSING")}


def get_nearest_track(net, x, y):
    """Find the nearest point on any trace segment of the given net.

    Returns {x, y, dist, seg_index, layer} or None.
    """
    with lock:
        tracks = state["tracks"]

    best_dist = float('inf')
    best = None

    for i, t in enumerate(tracks):
        if t["net"] != net:
            continue
        # Closest point on line segment (x1,y1)-(x2,y2) to point (x,y)
        ax, ay = t["x1"], t["y1"]
        bx, by = t["x2"], t["y2"]
        dx, dy = bx - ax, by - ay
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            px, py = ax, ay
        else:
            frac = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / len_sq))
            px = ax + frac * dx
            py = ay + frac * dy
        d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best = {
                "x": round(px, 2),
                "y": round(py, 2),
                "dist": round(d, 2),
                "seg_index": i,
                "layer": t["layer"]
            }

    return best or {"error": "no traces found for net"}


def get_orphan_vias(tol=0.15):
    """Find vias not connected to any trace endpoint."""
    with lock:
        vias = state["vias"]
        tracks = state["tracks"]

    # Collect all trace endpoints
    endpoints = set()
    for t in tracks:
        endpoints.add((round(t["x1"] / tol) * tol, round(t["y1"] / tol) * tol))
        endpoints.add((round(t["x2"] / tol) * tol, round(t["y2"] / tol) * tol))

    orphans = []
    for v in vias:
        vp = (round(v["x"] / tol) * tol, round(v["y"] / tol) * tol)
        if vp not in endpoints:
            orphans.append(v)

    return {"orphans": orphans, "total_vias": len(vias), "orphan_count": len(orphans)}


def get_clearance():
    """Return per-net minimum clearance to nearest foreign obstacle.

    For each routed net, walks every track segment sampling points along it,
    and finds the closest occupied grid cell belonging to a different net.
    Returns sorted list worst-first.
    """
    if not grid:
        return {"nets": []}

    from dpcb_router import GRID_PITCH, LAYER_IDS

    results = {}

    with lock:
        tracks = state["tracks"]

        # Group tracks by net
        net_tracks = {}
        for t in tracks:
            net_tracks.setdefault(t["net"], []).append(t)

        for net, segs in net_tracks.items():
            nid = grid.get_net_id(net)
            min_clear = float('inf')
            worst_x, worst_y = 0, 0
            worst_layer = ""

            for t in segs:
                layer_id = 0 if t["layer"] == "F.Cu" else 1
                gx1, gy1 = grid.mm_to_grid(t["x1"], t["y1"])
                gx2, gy2 = grid.mm_to_grid(t["x2"], t["y2"])

                # Sample points along segment
                dx = gx2 - gx1
                dy = gy2 - gy1
                steps = max(abs(dx), abs(dy), 1)
                for s in range(steps + 1):
                    frac = s / steps
                    cx = int(round(gx1 + dx * frac))
                    cy = int(round(gy1 + dy * frac))

                    # Search outward for nearest foreign obstacle
                    for radius in range(1, 20):
                        found = False
                        for ry in range(-radius, radius + 1):
                            for rx in range(-radius, radius + 1):
                                if abs(rx) != radius and abs(ry) != radius:
                                    continue  # only check perimeter
                                nx, ny = cx + rx, cy + ry
                                if 0 <= nx < grid.width and 0 <= ny < grid.height:
                                    occupant = grid.occupy[layer_id][ny, nx]
                                    if occupant != 0 and occupant != nid:
                                        dist_mm = (rx * rx + ry * ry) ** 0.5 * GRID_PITCH
                                        if dist_mm < min_clear:
                                            min_clear = dist_mm
                                            worst_x = t["x1"] + (t["x2"] - t["x1"]) * frac
                                            worst_y = t["y1"] + (t["y2"] - t["y1"]) * frac
                                            worst_layer = t["layer"]
                                        found = True
                        if found:
                            break

            if min_clear < float('inf'):
                results[net] = {
                    "net": net,
                    "min_clearance": round(min_clear, 3),
                    "worst_x": round(worst_x, 2),
                    "worst_y": round(worst_y, 2),
                    "worst_layer": worst_layer
                }

    sorted_nets = sorted(results.values(), key=lambda r: r["min_clearance"])
    return {"nets": sorted_nets}


def get_density(sector_size=10):
    """Return sector density map — occupancy % per layer per sector.

    Divides board into sectors of sector_size mm. Returns grid of sectors
    with F.Cu and B.Cu occupancy percentages and pad counts.
    """
    if not grid:
        return {"sectors": [], "cols": 0, "rows": 0, "sector_size": sector_size}

    from dpcb_router import GRID_PITCH
    board_w = grid.width * GRID_PITCH
    board_h = grid.height * GRID_PITCH
    ncols = max(1, int(board_w / sector_size + 0.5))
    nrows = max(1, int(board_h / sector_size + 0.5))
    cell_size = int(round(sector_size / GRID_PITCH))

    sectors = []
    with lock:
        for sr in range(nrows):
            row = []
            for sc in range(ncols):
                gx0 = sc * cell_size
                gy0 = sr * cell_size
                gx1 = min(gx0 + cell_size, grid.width)
                gy1 = min(gy0 + cell_size, grid.height)
                total = max(1, (gx1 - gx0) * (gy1 - gy0))

                fcu = 0
                bcu = 0
                for gy in range(gy0, gy1):
                    for gx in range(gx0, gx1):
                        if grid.occupy[0][gy, gx] != 0:
                            fcu += 1
                        if grid.occupy[1][gy, gx] != 0:
                            bcu += 1

                pads = sum(1 for p in state["pads"]
                           if sc * sector_size <= p["x"] < (sc + 1) * sector_size
                           and sr * sector_size <= p["y"] < (sr + 1) * sector_size)

                row.append({
                    "col": sc, "row": sr,
                    "x": sc * sector_size, "y": sr * sector_size,
                    "fcu": round(100 * fcu / total, 1),
                    "bcu": round(100 * bcu / total, 1),
                    "pads": pads
                })
            sectors.append(row)

    return {
        "sectors": sectors,
        "cols": ncols,
        "rows": nrows,
        "sector_size": sector_size,
        "board": [board_w, board_h]
    }


def handle_highlight(cmd):
    """Highlight a net in the viewer.

    cmd: {action: "highlight", net: "SPI_NSS"}
    Send net: null or net: "" to clear highlight.
    """
    net = cmd.get("net", "") or None
    with lock:
        state["highlight"] = net
        state["version"] += 1


def handle_mark(cmd):
    """Add a marker to the viewer.

    cmd: {action: "mark", x: 23.5, y: 8.0, color: "#ff0000", label: "here"}
    """
    marker = {
        "x": cmd.get("x", 0),
        "y": cmd.get("y", 0),
        "color": cmd.get("color", "#ff00ff"),
        "label": cmd.get("label", ""),
        "size": cmd.get("size", 1),
    }
    if "lx" in cmd and "ly" in cmd:
        marker["lx"] = cmd["lx"]
        marker["ly"] = cmd["ly"]
    with lock:
        state["markers"].append(marker)
        state["version"] += 1


def handle_clear_marks(cmd):
    """Clear all markers."""
    with lock:
        state["markers"] = []
        state["version"] += 1


def handle_move(cmd):
    """Move a component by adjusting its grid-cell col/row.

    cmd: {action: "move", ref: "XU1", dw: 2, dh: -1}
      dw = col delta (grid cells, +right)
      dh = row delta (grid cells, +down)
    """
    ref = cmd.get("ref", "")
    dw = cmd.get("dw", 0)
    dh = cmd.get("dh", 0)

    if not bloom_data or (not dw and not dh):
        return

    placement = bloom_data.get("placement")
    if not placement or ref not in placement:
        return

    with lock:
        p = placement[ref]
        p["col"] = max(0, p["col"] + dw)
        p["row"] = max(0, p["row"] + dh)

        _rebuild_from_placement()


def handle_place(cmd):
    """Place a component at an absolute grid-cell position.

    cmd: {action: "place", ref: "XU1", col: 10, row: 5}
    """
    ref = cmd.get("ref", "")
    col = cmd.get("col")
    row = cmd.get("row")

    if not bloom_data or col is None or row is None:
        return

    placement = bloom_data.get("placement")
    if not placement or ref not in placement:
        return

    with lock:
        p = placement[ref]
        p["col"] = max(0, int(col))
        p["row"] = max(0, int(row))

        _rebuild_from_placement()


def handle_rotate(cmd):
    """Rotate a component 90 degrees clockwise.

    cmd: {action: "rotate", ref: "XU1"}
    """
    ref = cmd.get("ref", "")

    if not bloom_data:
        return

    placement = bloom_data.get("placement")
    components = bloom_data.get("components", {})
    if not placement or ref not in placement or ref not in components:
        return

    with lock:
        # Update rotation in component data
        comp = components[ref]
        rot = (comp.get("rotation", 0) + 90) % 360
        comp["rotation"] = rot

        # Swap w/h in placement
        p = placement[ref]
        p["w"], p["h"] = p["h"], p["w"]

        _rebuild_from_placement()


def handle_place_via(cmd):
    """Place a via at a specific point for a net.

    cmd: {action: "place_via", net: "GND", x: 10.3, y: 34.0}
    """
    net = cmd.get("net", "")
    x = cmd.get("x", 0)
    y = cmd.get("y", 0)

    if not bloom_data:
        return

    with lock:
        state["vias"].append({
            "x": x, "y": y,
            "od": 0.6, "id": 0.3, "net": net
        })
        if grid:
            nid = grid.get_net_id(net)
            grid.mark_via(x, y, nid)
        state["version"] += 1


def _rebuild_from_placement():
    """Re-resolve all positions from placement. Call with lock held."""
    placement = bloom_data.get("placement")
    if not placement:
        return

    centres = resolve_positions(placement)
    rects = get_rects(placement)
    footprints = bloom_data.get("pcb", {}).get("footprints", {})
    components = bloom_data.get("components", {})

    state["components"] = {ref: {"x": round(x, 3), "y": round(y, 3)}
                           for ref, (x, y) in centres.items()}

    from bloom_grid import is_smd_package, rotate_pad
    pads = []
    for ref, comp in components.items():
        if ref not in centres:
            continue
        cx, cy = centres[ref]
        package = comp.get("package", "")
        rotation = comp.get("rotation", 0)
        smd = is_smd_package(package)
        pad_offsets = footprints.get(package, {}).get("pads", {})
        for pin_str, (dx, dy) in pad_offsets.items():
            rdx, rdy = rotate_pad(dx, dy, rotation)
            net = comp.get("pins", {}).get(pin_str, {}).get("net", "")
            pin_name = comp.get("pins", {}).get(pin_str, {}).get("name", pin_str)
            pads.append({
                "ref": ref, "pin": pin_str, "name": pin_name,
                "net": net, "x": round(cx + rdx, 3), "y": round(cy + rdy, 3),
                "smd": smd
            })

    state["pads"] = pads
    state["rects"] = rects
    state["version"] += 1
