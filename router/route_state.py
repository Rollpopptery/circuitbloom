"""Shared state, bloom loading/saving, and state-query helpers."""

import json
import math
import os
import sys
import threading
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'layout'))
sys.path.insert(0, os.path.dirname(__file__))

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
    "heatmap": None,  # base64 PNG data URL for copper heatmap
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


def build_router_grid_from_capture(data, origin_x, origin_y, board_w, board_h):
    """Build a RouterGrid from KiCad capture data.

    Args:
        data: capture data from grab_layer.capture_board()
        origin_x, origin_y: board origin offset (mm)
        board_w, board_h: board dimensions (mm)

    Returns:
        RouterGrid populated with pads, tracks, vias
    """
    from dpcb_router import RouterGrid, GRID_PITCH, LAYER_IDS
    import numpy as np

    # Get design rules from KiCad (or use defaults)
    rules = data.get("rules", {})
    clearance_mm = rules.get("clearance", 0.2)
    via_od_mm = rules.get("via_diameter", 0.6)
    via_id_mm = rules.get("via_drill", 0.3)

    clearance_cells = int(round(clearance_mm / GRID_PITCH))
    via_od_cells = int(round(via_od_mm / GRID_PITCH))
    via_id_cells = int(round(via_id_mm / GRID_PITCH))

    print(f"  Design rules: clearance={clearance_mm}mm, via_od={via_od_mm}mm, via_drill={via_id_mm}mm")

    g = RouterGrid(board_w, board_h, clearance_cells, via_od_cells, via_id_cells)

    # Build net_id map (1-indexed, 0 = no net)
    for i, net_name in enumerate(data["nets"]):
        g.net_ids[net_name] = i + 1

    # Build pad lookup: grid position -> (net_id, is_smd)
    pad_info = {}
    for p in data["pads"]:
        x = p["x"] - origin_x
        y = p["y"] - origin_y
        net_name = p["net"]
        nid = g.net_ids.get(net_name, 0)
        if nid == 0:
            nid = -1  # Unconnected pads are obstacles

        is_smd = p.get("smd", False)
        pad_layer = 0 if is_smd else None  # None = both layers (through-hole)

        gx, gy = g.mm_to_grid(x, y)
        g.pad_layers[(gx, gy)] = pad_layer
        pad_info[(gx, gy)] = (nid, is_smd)

    # Use actual pad shapes from copper grids if available
    fcu = data.get("fcu")
    bcu = data.get("bcu")

    if fcu is not None and bcu is not None:
        # Mark pads using actual copper shapes from KiCad
        # Grid value 1 = pad copper in the captured grids
        height, width = fcu.shape

        # Extra clearance around pads (based on design rules clearance)
        pad_clearance = clearance_cells  # Same as track clearance from rules

        # First pass: find all pad cells and their net assignments
        fcu_pad_cells = []
        bcu_pad_cells = []

        for gy in range(min(height, g.height)):
            for gx in range(min(width, g.width)):
                # Check F.Cu pad copper
                if fcu[gy, gx] == 1:
                    best_d = float('inf')
                    best_nid = -1
                    for (px, py), (nid, is_smd) in pad_info.items():
                        d = (gx - px) ** 2 + (gy - py) ** 2
                        if d < best_d:
                            best_d = d
                            best_nid = nid
                    if best_d < 400:  # Within ~2mm of a pad center
                        fcu_pad_cells.append((gx, gy, best_nid))

                # Check B.Cu pad copper
                if bcu[gy, gx] == 1:
                    best_d = float('inf')
                    best_nid = -1
                    for (px, py), (nid, is_smd) in pad_info.items():
                        if not is_smd:  # Only through-hole pads on B.Cu
                            d = (gx - px) ** 2 + (gy - py) ** 2
                            if d < best_d:
                                best_d = d
                                best_nid = nid
                    if best_d < 400:
                        bcu_pad_cells.append((gx, gy, best_nid))

        # Second pass: mark pad cells AND dilate with clearance
        for gx, gy, nid in fcu_pad_cells:
            for dy in range(-pad_clearance, pad_clearance + 1):
                for dx in range(-pad_clearance, pad_clearance + 1):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < g.width and 0 <= ny < g.height:
                        # Only mark if cell is empty (don't overwrite other pads)
                        if g.occupy[0][ny, nx] == 0:
                            g.occupy[0][ny, nx] = nid
                        g.pad_keepout.add((nx, ny))

        for gx, gy, nid in bcu_pad_cells:
            for dy in range(-pad_clearance, pad_clearance + 1):
                for dx in range(-pad_clearance, pad_clearance + 1):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < g.width and 0 <= ny < g.height:
                        if g.occupy[1][ny, nx] == 0:
                            g.occupy[1][ny, nx] = nid
                        g.pad_keepout.add((nx, ny))
    else:
        # Fallback: use circular pad approximation
        pad_r = 4  # 0.4mm radius
        for (gx, gy), (nid, is_smd) in pad_info.items():
            x_mm, y_mm = g.grid_to_mm(gx, gy)
            layers_to_mark = [0] if is_smd else [0, 1]
            for layer in layers_to_mark:
                g.mark_pad(x_mm, y_mm, pad_r, layer, nid)

            # Pad keepout for vias
            via_keepout_r = pad_r + 2
            for dy in range(-via_keepout_r, via_keepout_r + 1):
                for dx in range(-via_keepout_r, via_keepout_r + 1):
                    if dx * dx + dy * dy <= via_keepout_r * via_keepout_r:
                        g.pad_keepout.add((gx + dx, gy + dy))

    # Mark tracks
    for t in data["tracks"]:
        x1 = t["x1"] - origin_x
        y1 = t["y1"] - origin_y
        x2 = t["x2"] - origin_x
        y2 = t["y2"] - origin_y
        width = t.get("width", 0.25)
        layer_name = t.get("layer", "F.Cu")
        net_name = t.get("net", "")

        layer = LAYER_IDS.get(layer_name, 0)
        nid = g.net_ids.get(net_name, 0)
        w_cells = max(1, int(round(width / GRID_PITCH)))

        g.mark_track(x1, y1, x2, y2, w_cells, layer, nid)

    # Mark vias
    for v in data["vias"]:
        x = v["x"] - origin_x
        y = v["y"] - origin_y
        net_name = v.get("net", "")
        nid = g.net_ids.get(net_name, 0)
        g.mark_via(x, y, nid)

    return g


def reload_from_kicad(socket_path=None):
    """Load board state from KiCad via IPC API.

    Args:
        socket_path: KiCad socket path, or None to auto-detect

    Returns:
        (ok, message) tuple
    """
    global grid, kicad_socket

    try:
        from grab_layer import capture_board, find_socket, grid_to_png_base64
        from route_convert import net_color
    except ImportError as e:
        return False, f"import error: {e}"

    # Auto-detect socket if not provided
    if socket_path is None:
        socket_path = find_socket()
    if not socket_path:
        return False, "no KiCad socket found in /tmp/kicad/"

    # Save socket for push operations
    kicad_socket = socket_path

    try:
        data = capture_board(socket_path, pitch_mm=0.1)
    except Exception as e:
        return False, f"capture failed: {e}"

    # Build net color map
    nets = {}
    for net_name in data["nets"]:
        nets[net_name] = net_color(net_name)

    # Calculate board dimensions from bounds
    bounds = data["bounds"]
    origin_x = bounds["min_x"]
    origin_y = bounds["min_y"]
    board_w = bounds["max_x"] - origin_x
    board_h = bounds["max_y"] - origin_y

    # Offset coordinates to be relative to board origin
    pads = []
    for p in data["pads"]:
        pads.append({
            "ref": p["ref"],
            "pin": p["pin"],
            "net": p["net"],
            "x": round(p["x"] - origin_x, 3),
            "y": round(p["y"] - origin_y, 3),
            "smd": p["smd"],
            "name": p.get("name", p["pin"])
        })

    tracks = []
    for t in data["tracks"]:
        tracks.append({
            "x1": round(t["x1"] - origin_x, 3),
            "y1": round(t["y1"] - origin_y, 3),
            "x2": round(t["x2"] - origin_x, 3),
            "y2": round(t["y2"] - origin_y, 3),
            "width": t["width"],
            "layer": t["layer"],
            "net": t["net"]
        })

    vias = []
    for v in data["vias"]:
        vias.append({
            "x": round(v["x"] - origin_x, 3),
            "y": round(v["y"] - origin_y, 3),
            "od": v["od"],
            "id": v["id"],
            "net": v["net"]
        })

    components = {}
    for ref, pos in data["footprints"].items():
        components[ref] = {
            "x": round(pos["x"] - origin_x, 3),
            "y": round(pos["y"] - origin_y, 3)
        }

    # Generate copper heatmap PNG
    heatmap = None
    if "fcu" in data and "bcu" in data:
        heatmap = grid_to_png_base64(data["fcu"], data["bcu"], data["bounds"])

    with lock:
        state["pads"] = pads
        state["tracks"] = tracks
        state["vias"] = vias
        state["board"] = {
            "width": board_w,
            "height": board_h,
            "rules": data.get("rules", {}),
            "origin_x": origin_x,  # For converting back to KiCad coords
            "origin_y": origin_y,
            "heatmap_bounds": bounds
        }
        state["nets"] = nets
        state["components"] = components
        state["rects"] = []
        state["heatmap"] = heatmap
        state["version"] += 1

    # Build RouterGrid from captured data for routing capability
    grid = build_router_grid_from_capture(data, origin_x, origin_y, board_w, board_h)

    n_pads = len(data["pads"])
    n_tracks = len(data["tracks"])
    n_vias = len(data["vias"])
    n_nets = len(data["nets"])

    print(f"  KiCad capture: {n_pads} pads, {n_tracks} tracks, {n_vias} vias, {n_nets} nets")
    return True, f"captured {n_pads} pads, {n_tracks} tracks, {n_vias} vias, {n_nets} nets"


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


# Track last used KiCad socket for push operations
kicad_socket = None


def push_to_kicad(socket_path=None):
    """Push tracks and vias from state to KiCad.

    Args:
        socket_path: KiCad socket path, or None to use last captured socket

    Returns:
        (ok, message) tuple
    """
    global kicad_socket

    try:
        from grab_layer import find_socket
        from kicad_route import push_routes
    except ImportError as e:
        return False, f"import error: {e}"

    # Use provided socket, or last used, or auto-detect
    if socket_path:
        kicad_socket = socket_path
    elif not kicad_socket:
        kicad_socket = find_socket()

    if not kicad_socket:
        return False, "no KiCad socket found"

    with lock:
        tracks = state["tracks"]
        vias = state["vias"]
        board = state.get("board", {})
        origin_x = board.get("origin_x", 0)
        origin_y = board.get("origin_y", 0)

    if not tracks and not vias:
        return False, "no tracks or vias to push"

    try:
        ok, msg = push_routes(kicad_socket, tracks, vias, origin_x, origin_y)
        if ok:
            print(f"  KiCad push: {msg}")
        return ok, msg
    except Exception as e:
        return False, f"push failed: {e}"


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

    with lock:
        state["vias"].append({
            "x": x, "y": y,
            "od": 0.6, "id": 0.3, "net": net
        })
        if grid:
            nid = grid.get_net_id(net)
            grid.mark_via(x, y, nid)
        state["version"] += 1


def handle_set_footprint(cmd):
    """Set or modify the kicad_mod path for a package.

    cmd: {action: "set_footprint", package: "0805", kicad_mod: "LED_SMD.pretty/LED_0805_2012Metric.kicad_mod"}
    """
    package = cmd.get("package", "")
    kicad_mod = cmd.get("kicad_mod", "")

    if not bloom_data or not package:
        return {"ok": False, "error": "no bloom data or missing package name"}

    with lock:
        if "pcb" not in bloom_data:
            bloom_data["pcb"] = {}
        if "footprints" not in bloom_data["pcb"]:
            bloom_data["pcb"]["footprints"] = {}
        if package not in bloom_data["pcb"]["footprints"]:
            bloom_data["pcb"]["footprints"][package] = {}

        bloom_data["pcb"]["footprints"][package]["kicad_mod"] = kicad_mod
        state["version"] += 1

    return {"ok": True, "package": package, "kicad_mod": kicad_mod}


def get_footprints():
    """Return all footprint mappings (package -> kicad_mod path)."""
    if not bloom_data:
        return {"footprints": {}}

    footprints = bloom_data.get("pcb", {}).get("footprints", {})
    result = {}
    for package, fp_info in footprints.items():
        result[package] = {
            "kicad_mod": fp_info.get("kicad_mod", ""),
            "pads": len(fp_info.get("pads", {}))
        }
    return {"footprints": result}


# ============================================================
# DESIGN IMPACT — Before/After State for Routing Feedback
# ============================================================

def capture_design_state():
    """Capture current design metrics for before/after comparison.

    Returns:
        dict with:
            - clearances: {net: {min_clearance, worst_x, worst_y, worst_layer}}
            - overall_min_clearance: float
            - track_count: int
            - via_count: int
    """
    if not grid:
        return {
            "clearances": {},
            "overall_min_clearance": float('inf'),
            "track_count": 0,
            "via_count": 0
        }

    from dpcb_router import GRID_PITCH

    clearances = {}
    overall_min = float('inf')

    # Must be called with lock held or within lock context
    tracks = state["tracks"]
    vias = state["vias"]

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

            # Sample fewer points for speed (every 5 cells)
            dx = gx2 - gx1
            dy = gy2 - gy1
            steps = max(abs(dx), abs(dy), 1)
            sample_step = max(1, steps // 5)

            for s in range(0, steps + 1, sample_step):
                frac = s / steps if steps > 0 else 0
                cx = int(round(gx1 + dx * frac))
                cy = int(round(gy1 + dy * frac))

                # Search outward for nearest foreign obstacle (limit radius for speed)
                for radius in range(1, 15):
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
            clearances[net] = {
                "min_clearance": round(min_clear, 3),
                "worst_x": round(worst_x, 2),
                "worst_y": round(worst_y, 2),
                "worst_layer": worst_layer
            }
            if min_clear < overall_min:
                overall_min = min_clear

    return {
        "clearances": clearances,
        "overall_min_clearance": round(overall_min, 3) if overall_min < float('inf') else None,
        "track_count": len(tracks),
        "via_count": len(vias)
    }


def compute_design_impact(before, after, routed_net):
    """Compute the design impact of a routing action.

    Args:
        before: design state before routing (from capture_design_state)
        after: design state after routing (from capture_design_state)
        routed_net: the net that was just routed

    Returns:
        dict with:
            - overall_min: {before, after} - overall minimum clearance change
            - degraded: list of nets whose clearance got worse
            - improved: list of nets whose clearance got better (rare)
            - new_route: clearance info for the just-routed net
            - tracks_added: number of new track segments
            - vias_added: number of new vias
    """
    result = {
        "overall_min": {
            "before": before["overall_min_clearance"],
            "after": after["overall_min_clearance"]
        },
        "degraded": [],
        "improved": [],
        "new_route": None,
        "tracks_added": after["track_count"] - before["track_count"],
        "vias_added": after["via_count"] - before["via_count"]
    }

    # Check all nets for clearance changes
    all_nets = set(before["clearances"].keys()) | set(after["clearances"].keys())

    for net in all_nets:
        before_clear = before["clearances"].get(net, {})
        after_clear = after["clearances"].get(net, {})

        before_val = before_clear.get("min_clearance", float('inf'))
        after_val = after_clear.get("min_clearance", float('inf'))

        # Skip nets with no clearance data
        if before_val == float('inf') and after_val == float('inf'):
            continue

        if net == routed_net:
            # This is the newly routed net
            result["new_route"] = {
                "net": net,
                "clearance": None if after_val == float('inf') else after_val,
                "at": [after_clear.get("worst_x", 0), after_clear.get("worst_y", 0)],
                "layer": after_clear.get("worst_layer", "")
            }
        else:
            # Check if this net was affected by the routing
            delta = after_val - before_val
            threshold = 0.05  # 0.05mm change threshold

            # Convert infinity to None for JSON serialization
            before_json = None if before_val == float('inf') else before_val
            after_json = None if after_val == float('inf') else after_val

            if delta < -threshold:  # Clearance got worse (smaller)
                result["degraded"].append({
                    "net": net,
                    "before": before_json,
                    "after": after_json,
                    "delta": round(delta, 3),
                    "at": [after_clear.get("worst_x", 0), after_clear.get("worst_y", 0)],
                    "layer": after_clear.get("worst_layer", "")
                })
            elif delta > threshold and after_val != float('inf'):
                # Clearance got better — only report if net still has traces
                result["improved"].append({
                    "net": net,
                    "before": before_json,
                    "after": after_json,
                    "delta": round(delta, 3) if delta != float('inf') else None
                })

    # Sort degraded by delta (worst first)
    result["degraded"].sort(key=lambda x: x["delta"])

    return result


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
