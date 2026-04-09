"""Shared state, bloom loading/saving, and state-query helpers."""

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict

from route_optimise_r2 import optimise_pass as _optimise_r2_pass
from route_optimise_r1 import optimise_pass as _optimise_r1_pass
from route_optimise_junctions import optimise_pass as _optimise_junctions_pass

from route_optimise_cleanup import optimise_pass as _optimise_cleanup_pass


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'layout'))
sys.path.insert(0, os.path.dirname(__file__))

from bloom_grid import load_bloom, build_grid, get_pad_positions, get_net_map, get_component_centres
from tree_to_xy import transform as resolve_positions, get_rects
from route_convert import net_color


from dpcb_router_grid import GRID_PITCH, _line_cells_fast

# ============================================================
# STATE
# ============================================================

state = {
    "version": 0,
    "pads": [],
    "tracks": [],
    "vias": [],
    "board": {"width": 55, "height": 45},
    "nets": {},
    "components": {},
    "rects": [],
    "highlight": None,
    "markers": [],
    "heatmap": None,  # base64 PNG data URL for copper heatmap
}

bloom_data = None
bloom_path = None
board_path = None  # Path to .kicad_pcb file for CLI commands (DRC etc.)
grid = None
lock = threading.Lock()



def handle_check_via(cmd):
    """Check if a via can be placed at (x, y) for the given net.

    cmd: {action: "check_via", net: "GND", x: 5.0, y: 14.0}

    Returns {ok: True} if the via fits, {ok: False, message: ...} if not.
    """
    from dpcb_router_grid import GRID_PITCH
    if not grid:
        return {"ok": False, "message": "no grid"}

    net = cmd.get("net", "")
    x = float(cmd.get("x", 0))
    y = float(cmd.get("y", 0))
    nid_self = grid.net_ids.get(net, -999)

    gx, gy = grid.mm_to_grid(x, y)
    via_r = grid.via_od // 2
    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    scan_r = via_r + clearance_cells
    design_rule = clearance_cells * GRID_PITCH

    nid_to_name = {nid: name for name, nid in grid.net_ids.items()}

    for layer_id in range(grid.num_layers):
        occ_grid = grid.occupy[layer_id]
        for dy in range(-scan_r, scan_r + 1):
            for dx in range(-scan_r, scan_r + 1):
                nx, ny = gx + dx, gy + dy
                if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                    continue
                occ = int(occ_grid[ny, nx])
                if occ == 0 or occ == nid_self:
                    continue
                dx_out = max(0, abs(dx) - via_r)
                dy_out = max(0, abs(dy) - via_r)
                import math
                dist_mm = math.hypot(dx_out, dy_out) * GRID_PITCH
                if dist_mm < design_rule:
                    foreign = nid_to_name.get(occ, "<no-net>") if occ > 0 else "<no-net>"
                    hx, hy = grid.grid_to_mm(nx, ny)
                    return {
                        "ok": False,
                        "message": f"via clearance violation: {foreign} at [{round(hx,2)}, {round(hy,2)}]"
                    }

    return {"ok": True}


def handle_optimise_junctions(cmd):
    with lock:
        if not grid:
            return {"ok": False, "error": "no grid"}
    result = _optimise_junctions_pass(state, grid, lock)
    result["ok"] = True
    return result



def handle_optimise_cleanup(cmd):
    with lock:
        if not grid:
            return {"ok": False, "error": "no grid"}
    result = _optimise_cleanup_pass(state, grid, lock)
    result["ok"] = True
    return result

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
    from dpcb_router import GRID_PITCH
    from dpcb_router_grid import RouterGrid
    import numpy as np

    copper_layer_names = data.get("copper_layers", ["F.Cu", "B.Cu"])
    num_layers = len(copper_layer_names)
    layer_names = {i: name for i, name in enumerate(copper_layer_names)}
    layer_ids = {name: i for i, name in enumerate(copper_layer_names)}

    rules = data.get("rules", {})
    clearance_mm = rules.get("clearance", 0.2)
    via_od_mm = rules.get("via_diameter", 0.6)
    via_id_mm = rules.get("via_drill", 0.3)

    clearance_cells = int(round(clearance_mm / GRID_PITCH))
    via_od_cells = int(round(via_od_mm / GRID_PITCH))
    via_id_cells = int(round(via_id_mm / GRID_PITCH))

    print(f"  Design rules: clearance={clearance_mm}mm, via_od={via_od_mm}mm, via_drill={via_id_mm}mm")
    print(f"  Copper layers ({num_layers}): {copper_layer_names}")

    g = RouterGrid(board_w, board_h, clearance_cells, via_od_cells, via_id_cells,
                   num_layers=num_layers, layer_names=layer_names)

    for i, net_name in enumerate(data["nets"]):
        g.net_ids[net_name] = i + 1

    pad_info = {}
    for p in data["pads"]:
        x = p["x"] - origin_x
        y = p["y"] - origin_y
        net_name = p["net"]
        nid = g.net_ids.get(net_name, 0)
        if nid == 0:
            nid = -1

        is_smd = p.get("smd", False)
        if is_smd:
            pad_kicad_layers = p.get("layers", [])
            pad_layer = 0
            for lid in pad_kicad_layers:
                from grab_layer import BL_F_CU, BL_B_CU
                if lid == BL_B_CU:
                    pad_layer = layer_ids.get("B.Cu", num_layers - 1)
                elif lid == BL_F_CU:
                    pad_layer = 0
        else:
            pad_layer = None

        gx, gy = g.mm_to_grid(x, y)
        g.pad_layers[(gx, gy)] = pad_layer
        pad_info[(gx, gy)] = (nid, is_smd, pad_layer)

    copper_grids = data.get("copper_grids")
    if copper_grids is None:
        fcu = data.get("fcu")
        bcu = data.get("bcu")
        if fcu is not None and bcu is not None:
            copper_grids = {"F.Cu": fcu, "B.Cu": bcu}

    pad_owner_grids = data.get("pad_owner_grids")

    if copper_grids and pad_owner_grids:
        pads_list = data["pads"]
        g.pad_cells = {}

        # Mark pad copper in pad_grid (permanent — never cleared by routing)
        for router_layer in range(num_layers):
            layer_name = layer_names.get(router_layer)
            if layer_name is None or layer_name not in pad_owner_grids:
                continue
            owner = pad_owner_grids[layer_name]
            ys, xs = np.where(owner > 0)
            for gy, gx in zip(ys.tolist(), xs.tolist()):
                pad_idx = int(owner[gy, gx]) - 1
                if not (0 <= pad_idx < len(pads_list)):
                    continue
                p = pads_list[pad_idx]
                pnet = p.get("net", "")
                nid = g.net_ids.get(pnet, 0) if pnet else -1
                if nid == 0:
                    nid = -1
                if not (0 <= gx < g.width and 0 <= gy < g.height):
                    continue
                g.pad_cells.setdefault(router_layer, {}) \
                           .setdefault(nid, set()) \
                           .add((gx, gy))
                g.set_pad(router_layer, gx, gy, nid)
                g.pad_keepout.add((gx, gy))

        # Dilate pad copper in pad_grid by clearance_cells.
        # Marks clearance zone as permanent obstacle for foreign nets.
        # pad_grid is never cleared by routing — this zone is permanent.
        for router_layer in range(num_layers):
            layer_map = g.pad_cells.get(router_layer, {})
            for nid, cells in layer_map.items():
                for pgx, pgy in cells:
                    g.mark_pad_clearance(router_layer, pgx, pgy,
                                         nid, clearance_cells)

    else:
        # Fallback: circular pad approximation
        pad_r = 4
        for (gx, gy), (nid, is_smd, pad_layer) in pad_info.items():
            x_mm, y_mm = g.grid_to_mm(gx, gy)
            if is_smd:
                layers_to_mark = [pad_layer if pad_layer is not None else 0]
            else:
                layers_to_mark = list(range(num_layers))
            for layer in layers_to_mark:
                g.mark_pad(x_mm, y_mm, pad_r, layer, nid)

            via_keepout_r = pad_r + 2
            for dy in range(-via_keepout_r, via_keepout_r + 1):
                for dx in range(-via_keepout_r, via_keepout_r + 1):
                    if dx * dx + dy * dy <= via_keepout_r * via_keepout_r:
                        g.pad_keepout.add((gx + dx, gy + dy))

    # Mark tracks in route_grid
    for t in data["tracks"]:
        x1 = t["x1"] - origin_x
        y1 = t["y1"] - origin_y
        x2 = t["x2"] - origin_x
        y2 = t["y2"] - origin_y
        width = t.get("width", 0.25)
        layer_name = t.get("layer", "F.Cu")
        net_name = t.get("net", "")

        layer = g.layer_ids.get(layer_name)
        if layer is None:
            continue
        nid = g.net_ids.get(net_name, 0)
        w_cells = max(1, int(round(width / GRID_PITCH)))
        g.mark_track(x1, y1, x2, y2, w_cells, layer, nid)

    # Mark vias in route_grid
    for v in data["vias"]:
        x = v["x"] - origin_x
        y = v["y"] - origin_y
        net_name = v.get("net", "")
        nid = g.net_ids.get(net_name, 0)
        g.mark_via(x, y, nid)

    return g

def reload_from_kicad(socket_path=None):
    global grid, kicad_socket, board_path
    from reload_from_kicad import capture_from_kicad
    result = capture_from_kicad(socket_path, board_path_hint=board_path)
    if not result["ok"]:
        return False, result["message"]
    kicad_socket = result["socket"]
    if result["board_path"]:
        board_path = result["board_path"]
    grid = result["grid"]
    with lock:
        state.update(result["state"])
        state["version"] += 1
    return True, result["message"]



def open_board(path, socket_path=None):
    """Open a .kicad_pcb file in KiCad by restarting pcbnew, then capture it.

    Kills any running kicad/pcbnew, launches pcbnew with the new file,
    waits for the IPC API to become available, then captures the board.

    Args:
        path: Path to .kicad_pcb file (host filesystem)
        socket_path: KiCad socket path, or None to auto-detect after launch

    Returns:
        (ok, message) tuple
    """
    import time

    if not os.path.isfile(path):
        return False, f"file not found: {path}"

    try:
        # Kill existing KiCad and wait for sockets to close
        subprocess.run(["killall", "kicad", "pcbnew"],
                        capture_output=True, timeout=5)
        time.sleep(2)

        # Launch pcbnew with the new file
        subprocess.Popen(["pcbnew", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for KiCad IPC API to become available
        from kipy import KiCad
        connected = False
        for attempt in range(20):
            time.sleep(1)
            kicad = None
            try:
                kicad = KiCad(socket_path="ipc:///tmp/kicad/api.sock")
                kicad.ping()
                board = kicad.get_board()
                if board:
                    connected = True
                    break
            except Exception:
                pass
            finally:
                # Always close the connection attempt — prevent fd leak
                try:
                    if kicad is not None and hasattr(kicad, 'close'):
                        kicad.close()
                    elif kicad is not None and hasattr(kicad, '_channel'):
                        kicad._channel.close()
                except Exception:
                    pass

        if not connected:
            return False, "KiCad did not respond after 20 seconds"

    except Exception as e:
        return False, f"open failed: {e}"

    # Capture the newly loaded board
    result = reload_from_kicad("ipc:///tmp/kicad/api.sock")

    # Kill KiCad after capture to release all its file descriptors
    subprocess.run(["killall", "kicad", "pcbnew"],
                    capture_output=True, timeout=5)

    return result


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


def clear_net_in_kicad(net_name, socket_path=None):
    """Delete all tracks and vias for one net in KiCad via IPC.

    Does NOT touch server state — pair with `unroute` to keep both sides in sync.

    Args:
        net_name: Net to clear (required)
        socket_path: KiCad socket path, or None to use last captured / auto-detect

    Returns:
        (ok, message, counts) tuple
    """
    global kicad_socket

    if not net_name:
        return False, "net_name is required", {"tracks": 0, "vias": 0}

    try:
        from grab_layer import find_socket
        from kicad_route import clear_net
    except ImportError as e:
        return False, f"import error: {e}", {"tracks": 0, "vias": 0}

    if socket_path:
        kicad_socket = socket_path
    elif not kicad_socket:
        kicad_socket = find_socket()

    if not kicad_socket:
        return False, "no KiCad socket found", {"tracks": 0, "vias": 0}

    try:
        ok, msg, counts = clear_net(kicad_socket, net_name)
        if ok:
            print(f"  KiCad clear: {msg}")
        return ok, msg, counts
    except Exception as e:
        return False, f"clear failed: {e}", {"tracks": 0, "vias": 0}


# ============================================================
# STATE HELPERS
# ============================================================

def get_ratsnest():
    """Compute minimum spanning tree of pads for each net.
    
    Returns dict of net_name -> list of (from_pad, to_pad) pairs,
    where each pad is {ref, pin, x, y}.
    """
    with lock:
        pads = state["pads"]
    
    # Group pads by net
    net_pads = defaultdict(list)
    for p in pads:
        net = p.get("net", "")
        if net:
            net_pads[net].append(p)
    
    ratsnest = {}
    for net, np_list in net_pads.items():
        if len(np_list) < 2:
            continue
        
        # Prim's algorithm — minimum spanning tree
        connected = [np_list[0]]
        remaining = list(np_list[1:])
        edges = []
        
        while remaining:
            best_dist = float('inf')
            best_c = None
            best_r = None
            
            for c in connected:
                for r in remaining:
                    d = math.hypot(c["x"] - r["x"], c["y"] - r["y"])
                    if d < best_dist:
                        best_dist = d
                        best_c = c
                        best_r = r
            
            if best_r is None:
                break
            
            edges.append({
                "from": f"{best_c['ref']}.{best_c['pin']}",
                "to": f"{best_r['ref']}.{best_r['pin']}",
                "length_mm": round(best_dist, 2),
            })
            connected.append(best_r)
            remaining.remove(best_r)
        
        ratsnest[net] = edges
    
    return ratsnest



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
    """Audit every track segment endpoint and every layer transition.

    Two reports in one response:

    1. transitions — layer-change points. A point where tracks on two
       different layers share an endpoint. Reported as VIA (ok, a via is
       present at the point) or MISSING (needs a via).

    2. dangling — segment endpoints that touch nothing valid. An endpoint
       is valid iff at least one of:
         (a) the endpoint cell on its layer is owned by a pad of the same
             net in the rasterised grid (`grid.occupy[layer][gy, gx] == nid`);
         (b) a via of the same net sits at the endpoint (within tol);
         (c) another track endpoint of the same net and same layer sits
             at the endpoint (within tol) — i.e. a trunk joint.
       Anything else is a floating endpoint and is reported.

    This catches the failure mode where the A* router places a trace on a
    layer the endpoint pad does not exist on: both endpoints are on B.Cu,
    the pads are SMD on F.Cu, no vias are placed, and every coordinate /
    clearance / orphan-via check passes because each of those tools is
    blind to pad<->track electrical attachment.
    """
    with lock:
        tracks = list(state["tracks"])
        vias = list(state["vias"])

    # ---------------------------------------------------------------
    # 1. Layer transitions (preserves the historical behaviour).
    # ---------------------------------------------------------------
    endpoints = defaultdict(lambda: defaultdict(set))
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
        layer_names = list(layers.keys())
        for i in range(len(layer_names)):
            for j in range(i + 1, len(layer_names)):
                shared = layers[layer_names[i]] & layers[layer_names[j]]
                for pt in shared:
                    has_via = pt in via_pos
                    results.append({
                        "x": pt[0], "y": pt[1], "net": net,
                        "layers": [layer_names[i], layer_names[j]],
                        "status": "VIA" if has_via else "MISSING"
                    })

    # ---------------------------------------------------------------
    # 2. Dangling-endpoint audit.
    # ---------------------------------------------------------------
    # Index all endpoints per (net, layer) so we can detect joints
    # (same-net, same-layer, coincident endpoint from another segment end).
    endpoint_index = defaultdict(lambda: defaultdict(list))  # net -> layer -> [(x, y, seg_idx, which)]
    for idx, t in enumerate(tracks):
        net = t["net"]
        layer = t["layer"]
        endpoint_index[net][layer].append((t["x1"], t["y1"], idx, 1))
        endpoint_index[net][layer].append((t["x2"], t["y2"], idx, 2))

    def _touches_joint(net, layer, x, y, self_idx, self_which):
        for (ox, oy, oidx, owhich) in endpoint_index[net][layer]:
            if oidx == self_idx and owhich == self_which:
                continue
            if abs(ox - x) <= tol and abs(oy - y) <= tol:
                return True
        return False

    def _touches_via(net, x, y):
        for v in vias:
            if v.get("net") != net:
                continue
            if abs(v["x"] - x) <= tol and abs(v["y"] - y) <= tol:
                return True
        return False

    def _touches_pad(net, layer_name, x, y):
        # Authoritative: grid.pad_cells holds the cells that were marked
        # as real pad copper at capture time, layer-aware, per-nid. It is
        # never written by routing, so (unlike grid.occupy, which mixes
        # pad cells and trace cells under the same nid) it answers the
        # real question: "is this endpoint's cell inside a pad of the
        # same net on this layer?"
        if not grid:
            return None  # unknown — grid unavailable
        pad_cells = getattr(grid, "pad_cells", None)
        if not pad_cells:
            return None  # unknown — older grid without pad map
        nid = grid.net_ids.get(net)
        if nid is None:
            return False
        layer_id = grid.layer_ids.get(layer_name)
        if layer_id is None:
            return False
        layer_map = pad_cells.get(layer_id)
        if not layer_map:
            return False
        cells = layer_map.get(nid)
        if not cells:
            return False
        gx, gy = grid.mm_to_grid(x, y)
        return (gx, gy) in cells

    dangling = []
    grid_available = grid is not None
    for idx, t in enumerate(tracks):
        net = t["net"]
        layer = t["layer"]
        for which, (x, y) in ((1, (t["x1"], t["y1"])),
                              (2, (t["x2"], t["y2"]))):
            on_pad = _touches_pad(net, layer, x, y)
            on_via = _touches_via(net, x, y)
            on_joint = _touches_joint(net, layer, x, y, idx, which)
            if on_pad or on_via or on_joint:
                continue
            # Endpoint is floating. Build a reason string that names which
            # checks ran and what was missing, so downstream tooling can
            # distinguish "definitely dangling" from "pad check skipped".
            if on_pad is None:
                reason = "no via, no joint; pad check skipped (grid unavailable)"
            else:
                reason = "not on a pad, via, or joint of the same net"
            dangling.append({
                "net": net,
                "layer": layer,
                "x": round(x, 3),
                "y": round(y, 3),
                "track_index": idx,
                "endpoint": which,
                "reason": reason,
            })

    return {
        "transitions": results,
        "total": len(results),
        "missing": sum(1 for r in results if r["status"] == "MISSING"),
        "dangling": dangling,
        "dangling_count": len(dangling),
        "grid_available": grid_available,
    }


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


def _point_to_seg_dist(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def _seg_to_seg_dist(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Minimum distance between two line segments. Returns (dist, (mx, my))
    where (mx, my) is the midpoint of the closest approach on segment A."""
    # If segments intersect, distance is 0 and closest point is the crossing.
    d1x, d1y = ax2 - ax1, ay2 - ay1
    d2x, d2y = bx2 - bx1, by2 - by1
    denom = d1x * d2y - d1y * d2x
    if denom != 0:
        s = ((bx1 - ax1) * d2y - (by1 - ay1) * d2x) / denom
        t = ((bx1 - ax1) * d1y - (by1 - ay1) * d1x) / denom
        if 0 <= s <= 1 and 0 <= t <= 1:
            ix = ax1 + s * d1x
            iy = ay1 + s * d1y
            return 0.0, (ix, iy)
    # Otherwise min distance is at one of the four endpoint-to-segment distances.
    candidates = [
        (_point_to_seg_dist(ax1, ay1, bx1, by1, bx2, by2), (ax1, ay1)),
        (_point_to_seg_dist(ax2, ay2, bx1, by1, bx2, by2), (ax2, ay2)),
        (_point_to_seg_dist(bx1, by1, ax1, ay1, ax2, ay2), (bx1, by1)),
        (_point_to_seg_dist(bx2, by2, ax1, ay1, ax2, ay2), (bx2, by2)),
    ]
    best = min(candidates, key=lambda c: c[0])
    return best


def get_clearance():
    """Return all clearance violations for routed tracks.

    Uses the rasterised router grid as the authoritative geometry source.
    `grid.occupy[layer][y, x]` already contains each pad rasterised from its
    real KiCad polygon (no capture-time dilation). Clearance is enforced
    at query time: for each track cell, scan a neighbourhood of
    `grid.clearance` cells for foreign net copper.

    For each track segment, walk the cells it would occupy (Bresenham
    centreline dilated by the trace half-width, same as mark_track), and
    for each of those cells check the `clearance`-cell neighbourhood in
    `occupy[layer]` for any foreign net id or obstacle (-1). A hit is a
    clearance violation.

    Returns:
        {"design_rule_mm": float, "violations": [...], "clean": [...]}
    """
    if not grid:
        return {"design_rule_mm": 0.2, "violations": [], "clean": []}

    

    clearance_cells = int(getattr(grid, "clearance", 2) or 0)
    design_rule = round(clearance_cells * GRID_PITCH, 3)

    violations = []
    clean_set = set()
    dirty_set = set()

    with lock:
        tracks = state["tracks"]

        # nid -> net name reverse lookup
        nid_to_name = {nid: name for name, nid in grid.net_ids.items()}

        # Nearest-pad lookup for human-readable reporting: only pads on the
        # foreign net, keyed by foreign nid.
        foreign_pads = {}  # nid -> list of (gx, gy, ref_pin)
        for p in state["pads"]:
            pnet = p.get("net", "")
            pnid = grid.net_ids.get(pnet, 0) if pnet else -1
            gx, gy = grid.mm_to_grid(p["x"], p["y"])
            ref_pin = f"{p['ref']}.{p['pin']}" if p.get("pin") else p["ref"]
            foreign_pads.setdefault(pnid, []).append((gx, gy, ref_pin))

        def _nearest_pad_ref(nid, hx, hy):
            best = None
            best_d = None
            for px, py, ref in foreign_pads.get(nid, ()):
                d = (px - hx) * (px - hx) + (py - hy) * (py - hy)
                if best_d is None or d < best_d:
                    best_d = d
                    best = ref
            return best

        # Group tracks by net
        net_tracks = {}
        for t in tracks:
            net_tracks.setdefault(t.get("net", ""), []).append(t)

        for net, segs in net_tracks.items():
            nid_self = grid.net_ids.get(net, -999)
            net_violations = []

            for t in segs:
                layer_name = t.get("layer", "F.Cu")
                layer = grid.layer_ids.get(layer_name)
                if layer is None:
                    continue
                width = float(t.get("width", 0.25) or 0.25)
                w_cells = max(1, int(round(width / GRID_PITCH)))
                half_w = w_cells // 2

                gx1, gy1 = grid.mm_to_grid(t["x1"], t["y1"])
                gx2, gy2 = grid.mm_to_grid(t["x2"], t["y2"])

                # For every cell the track footprint covers, scan out to
                # (half_w + clearance_cells) and find the nearest foreign
                # copper. The distance reported is the true edge-to-edge
                # gap from the track's square footprint to the foreign
                # cell: zero when the foreign cell is inside the track
                # footprint (physical overlap), otherwise the Euclidean
                # distance between the two. Any distance strictly less
                # than the design-rule clearance is a violation.
                scan_r = half_w + clearance_cells

                # Best (smallest) distance per (foreign nid, near_pad)
                # so we only report the worst approach once per pair.
                best_hit = {}  # (occ, near_pad) -> (dist_mm, at_mm, layer_name)

                for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
                    for dy in range(-scan_r, scan_r + 1):
                        for dx in range(-scan_r, scan_r + 1):
                            nx, ny = cx + dx, cy + dy
                            if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                                continue
                            occ = int(grid.occupy[layer][ny, nx])
                            if occ == 0 or occ == nid_self:
                                continue
                            # Edge-to-edge distance from the track's
                            # square footprint (half_w cells around the
                            # centerline) to this foreign cell.
                            dx_out = max(0, abs(dx) - half_w)
                            dy_out = max(0, abs(dy) - half_w)
                            dist_cells = (dx_out * dx_out + dy_out * dy_out) ** 0.5
                            dist_mm = dist_cells * GRID_PITCH
                            if dist_mm >= design_rule:
                                continue  # within scan but not a violation
                            near_pad = _nearest_pad_ref(occ, nx, ny)
                            key = (occ, near_pad)
                            prev = best_hit.get(key)
                            if prev is None or dist_mm < prev[0]:
                                hx_mm, hy_mm = grid.grid_to_mm(nx, ny)
                                best_hit[key] = (dist_mm, (hx_mm, hy_mm), layer_name)

                for (occ, near_pad), (dist_mm, (hx_mm, hy_mm), layer_name_hit) in best_hit.items():
                    if occ > 0:
                        foreign_name = nid_to_name.get(occ, f"nid={occ}")
                    else:
                        foreign_name = "<no-net pad>"
                    net_violations.append({
                        "net": net,
                        "layer": layer_name_hit,
                        "at": [round(hx_mm, 2), round(hy_mm, 2)],
                        "near_net": foreign_name,
                        "near_pad": near_pad,
                        "distance_mm": round(dist_mm, 3),
                    })

            if net_violations:
                violations.extend(net_violations)
                dirty_set.add(net)
            else:
                clean_set.add(net)

    return {
        "design_rule_mm": design_rule,
        "violations": violations,
        "clean": sorted(clean_set - dirty_set),
    }


def diag_cells(x_mm, y_mm, layer_name, net_name, radius=6, margin=2):
    """Diagnostic dump of a small region of the grid around a point.

    Reports, for each cell in a (2*radius+1) window centred on (x_mm,
    y_mm) on the given layer:
      - occ: grid.occupy[layer][y, x] (nid of whatever owns the cell,
        0 = empty, -1 = no-net obstacle)
      - pad: True if (gx, gy) is in grid.pad_cells[layer][nid_of_net]
        (authoritative pad copper, never written by routing)
      - blocked: True if build_blocked_grid(nid_of_net, margin) marks
        this cell as blocked for A*

    Lets us tell apart "pad not marked", "marked as own net", "foreign
    dilation reaches here", "own-pad cell but still blocked" — which is
    the class of question we keep hitting on tight-pitch pad exits.
    """
    if not grid:
        return {"ok": False, "error": "no grid"}
    nid = grid.net_ids.get(net_name)
    if nid is None:
        return {"ok": False, "error": f"unknown net {net_name}"}
    layer_id = grid.layer_ids.get(layer_name)
    if layer_id is None:
        return {"ok": False, "error": f"unknown layer {layer_name}"}
    cx, cy = grid.mm_to_grid(x_mm, y_mm)
    blocked = grid.build_blocked_grid(nid, margin)
    pad_set = getattr(grid, "pad_cells", {}).get(layer_id, {}).get(nid, set())
    nid_to_name = {n: name for name, n in grid.net_ids.items()}
    rows = []
    for dy in range(-radius, radius + 1):
        row = []
        gy = cy + dy
        if not (0 <= gy < grid.height):
            rows.append([])
            continue
        for dx in range(-radius, radius + 1):
            gx = cx + dx
            if not (0 <= gx < grid.width):
                row.append(None)
                continue
            occ = int(grid.occupy[layer_id][gy, gx])
            row.append({
                "gx": gx, "gy": gy,
                "occ": occ,
                "occ_name": nid_to_name.get(occ, "") if occ > 0 else ("" if occ == 0 else "<obstacle>"),
                "pad": (gx, gy) in pad_set,
                "blocked": bool(blocked[layer_id][gy, gx]),
            })
        rows.append(row)
    return {
        "ok": True,
        "center_mm": [x_mm, y_mm],
        "center_grid": [cx, cy],
        "layer": layer_name,
        "net": net_name,
        "nid": nid,
        "clearance_cells": int(getattr(grid, "clearance", 2) or 0),
        "margin": margin,
        "rows": rows,
    }


def get_density(sector_size=10):
    """Return sector density map — occupancy % per layer per sector.

    Divides board into sectors of sector_size mm. Returns grid of sectors
    with per-layer occupancy percentages and pad counts.
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

                layer_counts = [0] * grid.num_layers
                for gy in range(gy0, gy1):
                    for gx in range(gx0, gx1):
                        for li in range(grid.num_layers):
                            if grid.occupy[li][gy, gx] != 0:
                                layer_counts[li] += 1

                pads = sum(1 for p in state["pads"]
                           if sc * sector_size <= p["x"] < (sc + 1) * sector_size
                           and sr * sector_size <= p["y"] < (sr + 1) * sector_size)

                sector = {
                    "col": sc, "row": sr,
                    "x": sc * sector_size, "y": sr * sector_size,
                    "pads": pads
                }
                for li in range(grid.num_layers):
                    name = grid.layer_names.get(li, f'L{li}')
                    safe_name = name.replace('.', '_').lower()
                    sector[safe_name] = round(100 * layer_counts[li] / total, 1)
                # Backward compat
                if grid.num_layers >= 1:
                    sector.setdefault("fcu", sector.get("f_cu", 0))
                if grid.num_layers >= 2:
                    bcu_key = grid.layer_names.get(grid.num_layers - 1, "B.Cu").replace('.', '_').lower()
                    sector.setdefault("bcu", sector.get(bcu_key, 0))
                row.append(sector)
            sectors.append(row)

    return {
        "sectors": sectors,
        "cols": ncols,
        "rows": nrows,
        "sector_size": sector_size,
        "board": [board_w, board_h]
    }


# SSE subscribers for real-time highlight push
_sse_subscribers = []
_sse_lock = threading.Lock()


def sse_subscribe(wfile):
    """Register a viewer connection for SSE events."""
    with _sse_lock:
        _sse_subscribers.append(wfile)


def sse_unsubscribe(wfile):
    """Remove a viewer connection."""
    with _sse_lock:
        try:
            _sse_subscribers.remove(wfile)
        except ValueError:
            pass


def _sse_broadcast(event_type, data):
    """Send an SSE event to all connected viewers."""
    import json
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    encoded = msg.encode()
    with _sse_lock:
        dead = []
        for wfile in _sse_subscribers:
            try:
                wfile.write(encoded)
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for w in dead:
            try:
                _sse_subscribers.remove(w)
            except ValueError:
                pass


def handle_highlight(cmd):
    """Highlight a net in the viewer via SSE push.

    cmd: {action: "highlight", net: "SPI_NSS"}
    Send net: null or net: "" to clear highlight.
    """
    net = cmd.get("net", "") or None
    color = cmd.get("color", "#ffff00")
    with lock:
        state["highlight"] = net
    _sse_broadcast("highlight", {"net": net, "color": color})


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
    x = float(cmd.get("x", 0) or 0)
    y = float(cmd.get("y", 0) or 0)

    with lock:
        state["vias"].append({
            "x": x, "y": y,
            "od": 0.6, "id": 0.3, "net": net
        })
        if grid:
            nid = grid.get_net_id(net)
            grid.mark_via(x, y, nid)
        state["version"] += 1


def find_via_spot(net, x_mm, y_mm, margin=3, min_radius=10, max_radius=50):
    """Wrapper — delegates to find_via module, passing the current grid."""
    from find_via import find_via_spot as _find
    return _find(grid, net, x_mm, y_mm, margin, min_radius, max_radius)

def handle_add_track(cmd):
    """Add a single track segment.

    cmd: {action: "add_track", net: "GND", x1: 5.0, y1: 14.0, x2: 5.0, y2: 22.0,
          layer: "B.Cu", width: 0.25}
    """
    from dpcb_router import GRID_PITCH
    net = cmd.get("net", "")
    seg = {
        "x1": float(cmd.get("x1", 0) or 0),
        "y1": float(cmd.get("y1", 0) or 0),
        "x2": float(cmd.get("x2", 0) or 0),
        "y2": float(cmd.get("y2", 0) or 0),
        "width": float(cmd.get("width", 0.25) or 0.25),
        "layer": cmd.get("layer", "F.Cu"),
        "net": net
    }

    with lock:
        if grid:
            
            nid_self = grid.net_ids.get(net, -999)
            layer_id = grid.layer_ids.get(seg["layer"], 0)
            w = max(1, int(round(seg["width"] / GRID_PITCH)))
            half_w = w // 2
            clearance_cells = int(getattr(grid, "clearance", 2) or 0)
            scan_r = half_w + clearance_cells
            design_rule = clearance_cells * GRID_PITCH
            gx1, gy1 = grid.mm_to_grid(seg["x1"], seg["y1"])
            gx2, gy2 = grid.mm_to_grid(seg["x2"], seg["y2"])
            nid_to_name = {nid: name for name, nid in grid.net_ids.items()}
            hit = None
            best_dist = float("inf")

            occ_grid = grid.occupy[layer_id]

            for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
                for dy in range(-scan_r, scan_r + 1):
                    for dx in range(-scan_r, scan_r + 1):
                        nx, ny = cx + dx, cy + dy
                        if not (0 <= nx < grid.width and 0 <= ny < grid.height):
                            continue
                        # Use get_cell() — checks both pad_grid and route_grid
                        occ = int(occ_grid[ny, nx])
                        
                        if occ == 0 or occ == nid_self:
                            continue
                        dx_out = max(0, abs(dx) - half_w)
                        dy_out = max(0, abs(dy) - half_w)
                        dist_cells = (dx_out * dx_out + dy_out * dy_out) ** 0.5
                        dist_mm = dist_cells * GRID_PITCH
                        if dist_mm >= design_rule:
                            continue
                        if dist_mm < best_dist:
                            best_dist = dist_mm
                            foreign = nid_to_name.get(occ, "<no-net pad>") if occ > 0 else "<no-net pad>"
                            hx, hy = grid.grid_to_mm(nx, ny)
                            hit = {
                                "at": [round(hx, 2), round(hy, 2)],
                                "layer": seg["layer"],
                                "near_net": foreign,
                                "distance_mm": round(dist_mm, 3),
                            }

            if hit:
                return {
                    "ok": False,
                    "error": "clearance_violation",
                    "message": (
                        f"refused: track on net '{net}' would collide with "
                        f"{hit['near_net']} at {hit['at']} ({hit['layer']})"
                    ),
                    "violation": hit,
                    "track": seg,
                }

        state["tracks"].append(seg)
        if grid:
            nid = grid.get_net_id(net)
            layer_id = grid.layer_ids.get(seg["layer"], 0)
            w = max(1, int(round(seg["width"] / GRID_PITCH)))
            grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                            w, layer_id, nid)
        state["version"] += 1

    return {"ok": True, "track": seg}

def handle_delete_tracks(cmd):
    """Delete track segments within a region.

    cmd: {action: "delete_tracks", net: "GND",
          x_min: 2, y_min: 12, x_max: 12, y_max: 24}

    Deletes segments where BOTH endpoints fall within the bounding box.
    If net is provided, only deletes segments on that net.
    """
    
    net = cmd.get("net", "")
    x_min = cmd.get("x_min", -1e9)
    y_min = cmd.get("y_min", -1e9)
    x_max = cmd.get("x_max", 1e9)
    y_max = cmd.get("y_max", 1e9)

    def in_box(x, y):
        return x_min <= x <= x_max and y_min <= y <= y_max

    with lock:
        kept = []
        removed = 0
        for t in state["tracks"]:
            if (not net or t["net"] == net) and \
               in_box(t["x1"], t["y1"]) and in_box(t["x2"], t["y2"]):
                # Clear from grid
                if grid:
                    nid = grid.get_net_id(t["net"])
                    layer_id = grid.layer_ids.get(t["layer"], 0)
                    w = max(1, int(round(float(t.get("width", 0.25) or 0.25) / GRID_PITCH)))
                    gx1, gy1 = grid.mm_to_grid(t["x1"], t["y1"])
                    gx2, gy2 = grid.mm_to_grid(t["x2"], t["y2"])
                    hw = w // 2
                    for cx, cy in _line_cells_fast(gx1, gy1, gx2, gy2):
                        for dy in range(-hw, hw + 1):
                            for dx in range(-hw, hw + 1):
                                grid.clear_cell(layer_id, cx + dx, cy + dy, nid)
                removed += 1
            else:
                kept.append(t)
        state["tracks"] = kept
        if removed:
            state["version"] += 1

    return {"ok": True, "removed": removed}


def handle_delete_via(cmd):
    """Delete vias within a region.

    cmd: {action: "delete_via", net: "GND",
          x_min: 2, y_min: 12, x_max: 12, y_max: 24}

    If net is provided, only deletes vias on that net.
    """
    net = cmd.get("net", "")
    x_min = cmd.get("x_min", -1e9)
    y_min = cmd.get("y_min", -1e9)
    x_max = cmd.get("x_max", 1e9)
    y_max = cmd.get("y_max", 1e9)

    with lock:
        kept = []
        removed = 0
        for v in state["vias"]:
            if (not net or v["net"] == net) and \
               x_min <= v["x"] <= x_max and y_min <= v["y"] <= y_max:
                if grid:
                    gx, gy = grid.mm_to_grid(v["x"], v["y"])
                    r = grid.via_od // 2
                    for layer in range(grid.num_layers):
                        grid.mark_circle(layer, gx, gy, r, 0)
                removed += 1
            else:
                kept.append(v)
        state["vias"] = kept
        if removed:
            state["version"] += 1

    return {"ok": True, "removed": removed}


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
            layer_id = grid.layer_ids.get(t["layer"], 0)
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

  

def handle_optimise_r1(cmd):
    step = float(cmd.get("step_mm", 0.5))
    with lock:
        if not grid:
            return {"ok": False, "error": "no grid"}
    result = _optimise_r1_pass(state, grid, lock, step)
    result["ok"] = True
    return result

def handle_optimise_r2(cmd):
    with lock:
        if not grid:
            return {"ok": False, "error": "no grid"}
    result = _optimise_r2_pass(state, grid, lock)
    result["ok"] = True
    return result


# ============================================================
# DRC — KiCad CLI Design Rule Check
# ============================================================

def run_drc(cmd=None):
    """Run kicad-cli pcb drc on the board file.

    cmd options:
        schematic_parity (bool): include schematic parity check
        all_track_errors (bool): report all errors per track
        refill_zones (bool): refill zones before DRC
        severity (str): "all", "error", "warning", or "exclusions"

    Returns (ok, result_dict).
    """
    if not board_path:
        return False, {"error": "no board file configured (use --board flag)"}

    if not os.path.isfile(board_path):
        return False, {"error": f"board file not found: {board_path}"}

    cmd = cmd or {}

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name

    try:
        args = ["kicad-cli", "pcb", "drc",
                "--format", "json",
                "--units", "mm",
                "-o", out_path]

        severity = cmd.get("severity", "all")
        if severity == "all":
            args.append("--severity-all")
        elif severity == "error":
            args.append("--severity-error")
        elif severity == "warning":
            args.append("--severity-warning")
        elif severity == "exclusions":
            args.append("--severity-exclusions")

        if cmd.get("all_track_errors"):
            args.append("--all-track-errors")
        if cmd.get("schematic_parity"):
            args.append("--schematic-parity")
        if cmd.get("refill_zones"):
            args.append("--refill-zones")

        args.append("--exit-code-violations")
        args.append(board_path)

        result = subprocess.run(args, capture_output=True, text=True, timeout=120)

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path) as f:
                drc_report = json.load(f)
        else:
            return False, {
                "error": "DRC produced no output",
                "stderr": result.stderr,
                "returncode": result.returncode
            }

        violations = drc_report.get("violations", [])
        has_violations = result.returncode != 0

        return True, {
            "violations": len(violations),
            "has_errors": has_violations,
            "report": drc_report,
            "returncode": result.returncode
        }

    except FileNotFoundError:
        return False, {"error": "kicad-cli not found — is KiCad installed and on PATH?"}
    except subprocess.TimeoutExpired:
        return False, {"error": "DRC timed out (120s)"}
    except Exception as e:
        return False, {"error": str(e)}
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)
