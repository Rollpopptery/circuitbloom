
"""Agent handler for port 8084."""
import http.server
import json

from dpcb_router import route_by_name, route_tap_by_name, GRID_PITCH, _line_cells
from dpcb_router8 import route8_by_name
from route_convert import path_to_segments, path_vias
import route_state as rs
from route_state import (
    state, lock,
    reload_bloom, reload_from_kicad, save_bloom, push_to_kicad,
    clear_net_in_kicad, snap_to_pad, get_transitions, get_orphan_vias,
    get_clearance, get_density, get_nearest_track,
    handle_move, handle_place, handle_rotate, handle_place_via,
    handle_add_track, handle_delete_tracks, handle_delete_via,
    handle_highlight, handle_mark, handle_clear_marks, handle_set_footprint,
    get_footprints, capture_design_state, compute_design_impact,
    run_drc, find_via_spot, open_board, diag_cells, get_ratsnest,
    handle_optimise_r1, handle_optimise_r2, handle_optimise_junctions,
    handle_optimise_cleanup, handle_check_via
)


def _coerce_xy(v):
    """Coerce an (x, y) pair from MCP/JSON input into two floats."""
    if isinstance(v, str):
        s = v.strip()
        try:
            v = json.loads(s)
        except Exception:
            parts = [p for p in s.replace("[", " ").replace("]", " ").replace(",", " ").split() if p]
            v = parts
    if not isinstance(v, (list, tuple)):
        return 0.0, 0.0
    x = float(v[0]) if len(v) > 0 else 0.0
    y = float(v[1]) if len(v) > 1 else 0.0
    return x, y


class AgentHandler(http.server.BaseHTTPRequestHandler):

    # ============================================================
    # GET
    # ============================================================

    def do_GET(self):
        if self.path == '/version':
            self._json_response({"v": state["version"]})
        elif self.path == '/save':
            ok, msg = save_bloom()
            self._json_response({"ok": ok, "message": msg})
        elif self.path == '/nets':
            self._json_response(state["nets"])
        elif self.path == '/pads':
            self._json_response(state["pads"])
        elif self.path.startswith('/pads/'):
            net = self.path[6:]
            pads = [p for p in state["pads"] if p["net"] == net]
            self._json_response(pads)
        elif self.path == '/ratsnest':
            self._json_response(get_ratsnest())
        elif self.path == '/status':
            _grid = rs.grid
            with lock:
                s = _grid.stats() if _grid else {}
                s["tracks"] = len(state["tracks"])
                s["vias"] = len(state["vias"])
                s["pads"] = len(state["pads"])
                s["nets"] = len(state["nets"])
            self._json_response(s)
        elif self.path == '/reload':
            reload_bloom()
            self._json_response({"ok": True, "v": state["version"]})
        elif self.path == '/capture_kicad' or self.path.startswith('/capture_kicad?'):
            import urllib.parse
            socket_path = None
            if '?' in self.path:
                params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
                socket_path = params.get('socket', [None])[0]
            ok, msg = reload_from_kicad(socket_path)
            self._json_response({"ok": ok, "message": msg, "v": state["version"]})
        elif self.path == '/push_kicad' or self.path.startswith('/push_kicad?'):
            import urllib.parse
            socket_path = None
            if '?' in self.path:
                params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
                socket_path = params.get('socket', [None])[0]
            ok, msg = push_to_kicad(socket_path)
            self._json_response({"ok": ok, "message": msg})
        elif self.path.startswith('/get_transitions'):
            self._json_response(get_transitions(0.15))
        elif self.path == '/get_vias':
            self._json_response(state["vias"])
        elif self.path == '/density':
            self._json_response(get_density(10))
        elif self.path == '/clearance':
            self._json_response(get_clearance())
        elif self.path.startswith('/nearest_track?'):
            import urllib.parse
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            net = params.get('net', [''])[0]
            x = float(params.get('x', [0])[0])
            y = float(params.get('y', [0])[0])
            self._json_response(get_nearest_track(net, x, y))
        elif self.path == '/orphan_vias':
            self._json_response(get_orphan_vias())
        elif self.path.startswith('/diag_cells?'):
            import urllib.parse
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            x = float(params.get('x', [0])[0])
            y = float(params.get('y', [0])[0])
            layer = params.get('layer', ['F.Cu'])[0]
            net = params.get('net', [''])[0]
            radius = int(params.get('radius', [6])[0])
            margin = int(params.get('margin', [2])[0])
            self._json_response(diag_cells(x, y, layer, net, radius, margin))
        elif self.path == '/placement':
            p = rs.bloom_data.get("placement", {}) if rs.bloom_data else {}
            self._json_response(p)
        elif self.path.startswith('/placement/'):
            ref = self.path[11:]
            p = rs.bloom_data.get("placement", {}) if rs.bloom_data else {}
            if ref in p:
                self._json_response({ref: p[ref]})
            else:
                self._json_response({"error": f"unknown ref: {ref}"}, 404)
        elif self.path == '/footprints':
            self._json_response(get_footprints())
        elif self.path == '/drc':
            ok, result = run_drc()
            self._json_response({"ok": ok, **result})
        elif self.path.startswith('/render'):
            import urllib.parse
            from board_render import render_board
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            x1 = float(params['x1'][0]) if 'x1' in params else None
            y1 = float(params['y1'][0]) if 'y1' in params else None
            x2 = float(params['x2'][0]) if 'x2' in params else None
            y2 = float(params['y2'][0]) if 'y2' in params else None
            w = int(params.get('width', params.get('w', [600]))[0])
            with lock:
                png = render_board(state, x1=x1, y1=y1, x2=x2, y2=y2, width=w)
            if png:
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(png)
            else:
                self._json_response({"error": "render failed"}, 500)
        elif self.path.startswith('/pad_info?'):
            import urllib.parse
            from pad_info import get_pad_info
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            ref = params.get('ref', [''])[0]
            pin = params.get('pin', [''])[0]
            self._json_response(get_pad_info(ref, pin))
        elif self.path.startswith('/component_info?'):
            import urllib.parse
            from pad_info import get_component_info
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            ref = params.get('ref', [''])[0]
            self._json_response(get_component_info(ref))
        elif self.path.startswith('/route_examples?'):
            import urllib.parse
            from route_examples import search as route_examples_search
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            query = params.get('q', [''])[0]
            n = int(params.get('n', [5])[0])
            board = params.get('board', [None])[0]
            self._json_response(route_examples_search(query, n, board))
        elif self.path.startswith('/find_via_spot?'):
            import urllib.parse
            params = urllib.parse.parse_qs(self.path.split('?', 1)[1])
            net = params.get('net', [''])[0]
            x = float(params.get('x', [0])[0])
            y = float(params.get('y', [0])[0])
            margin = int(params.get('margin', [3])[0])
            min_r = int(params.get('min_radius', [10])[0])
            max_r = int(params.get('max_radius', [50])[0])
            self._json_response(find_via_spot(net, x, y, margin, min_r, max_r))
        else:
            self._json_response(state)

    # ============================================================
    # POST
    # ============================================================

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            cmd = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            self._json_response({"ok": False, "error": str(e)}, 400)
            return

        action = cmd.get("action", "")

        if action == "route":
            self._handle_route(cmd)
        elif action == "route_tap":
            self._handle_route_tap(cmd)
        elif action == "unroute":
            self._handle_unroute(cmd)
        elif action == "move":
            handle_move(cmd)
            self._json_response({"ok": True})
        elif action == "place":
            handle_place(cmd)
            self._json_response({"ok": True})
        elif action == "rotate":
            handle_rotate(cmd)
            self._json_response({"ok": True})
        elif action == "place_via":
            handle_place_via(cmd)
            self._json_response({"ok": True})
        elif action == "add_track":
            result = handle_add_track(cmd)
            self._json_response(result)
        elif action == "delete_tracks":
            result = handle_delete_tracks(cmd)
            self._json_response(result)
        elif action == "delete_via":
            result = handle_delete_via(cmd)
            self._json_response(result)
        elif action == "highlight":
            handle_highlight(cmd)
            self._json_response({"ok": True})
        elif action == "mark":
            handle_mark(cmd)
            self._json_response({"ok": True})
        elif action == "clear_marks":
            handle_clear_marks(cmd)
            self._json_response({"ok": True})
        elif action == "set_footprint":
            result = handle_set_footprint(cmd)
            self._json_response(result)
        elif action == "save":
            ok, msg = save_bloom()
            self._json_response({"ok": ok, "message": msg})
        elif action == "reload":
            reload_bloom()
            self._json_response({"ok": True, "v": state["version"]})
        elif action == "capture_kicad":
            socket_path = cmd.get("socket", None)
            ok, msg = reload_from_kicad(socket_path)
            self._json_response({"ok": ok, "message": msg, "v": state["version"]})
        elif action == "push_kicad":
            socket_path = cmd.get("socket", None)
            ok, msg = push_to_kicad(socket_path)
            self._json_response({"ok": ok, "message": msg})
        elif action == "clear_kicad_net":
            net_name = cmd.get("net", "")
            socket_path = cmd.get("socket", None)
            ok, msg, counts = clear_net_in_kicad(net_name, socket_path)
            self._json_response({"ok": ok, "message": msg, **counts})
        elif action == "drc":
            ok, result = run_drc(cmd)
            self._json_response({"ok": ok, **result})
        elif action == "open_board":
            path = cmd.get("path", "")
            socket_path = cmd.get("socket", None)
            ok, msg = open_board(path, socket_path)
            self._json_response({"ok": ok, "message": msg, "v": state["version"]})
        elif action == "optimise_r1":
            self._json_response(handle_optimise_r1(cmd))
        elif action == "optimise_r2":
            self._json_response(handle_optimise_r2(cmd))
        elif action == "optimise_junctions":
            self._json_response(handle_optimise_junctions(cmd))
        elif action == "optimise_cleanup":
            self._json_response(handle_optimise_cleanup(cmd))
        elif action == "check_via":
            result = handle_check_via(cmd)
            self._json_response(result)
        else:
            self._json_response({"ok": False, "error": f"unknown action: {action}"}, 400)

    # ============================================================
    # ROUTE ACTIONS
    # ============================================================

    def _handle_route(self, cmd):
        _grid = rs.grid
        net = cmd.get("net", "")
        x1, y1 = _coerce_xy(cmd.get("from", [0, 0]))
        x2, y2 = _coerce_xy(cmd.get("to", [0, 0]))
        layer = cmd.get("layer", "auto")
        margin = int(cmd.get("margin", 3) or 3)
        use8 = cmd.get("use8", False)
        if isinstance(use8, str):
            use8 = use8.lower() in ("true", "1", "yes")

        x1, y1 = snap_to_pad(x1, y1, net)
        x2, y2 = snap_to_pad(x2, y2, net)

        with lock:
            before_state = capture_design_state()
            if use8:
                result = route8_by_name(_grid, net, x1, y1, x2, y2,
                                        layer_mode=layer, margin_override=margin)
            else:
                result = route_by_name(_grid, net, x1, y1, x2, y2,
                                       layer_mode=layer, margin_override=margin)
            if result.success:
                segments = path_to_segments(_grid, result.path, net,
                                            _grid.layer_names,
                                            cmd.get("width", 0.25),
                                            start_mm=(x1, y1),
                                            end_mm=(x2, y2))
                state["tracks"].extend(segments)
                nid = _grid.get_net_id(net)
                for seg in segments:
                    layer_id = _grid.layer_ids.get(seg["layer"], 0)
                    w = max(1, int(round(seg["width"] / GRID_PITCH)))
                    _grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                                     w, layer_id, nid)
                via_positions = path_vias(result.path)
                for vx, vy in via_positions:
                    vx_mm, vy_mm = _grid.grid_to_mm(vx, vy)
                    state["vias"].append({"x": vx_mm, "y": vy_mm,
                                          "od": 0.6, "id": 0.3, "net": net})
                    _grid.mark_via(vx_mm, vy_mm, nid)
                state["version"] += 1
            after_state = capture_design_state()
            impact = compute_design_impact(before_state, after_state, net)

        self._json_response({
            "ok": result.success,
            "message": result.message,
            "length": result.length_mm,
            "vias": result.via_count,
            "segments": result.segment_count,
            "impact": impact,
        })

    def _handle_route_tap(self, cmd):
        _grid = rs.grid
        net = cmd.get("net", "")
        x1, y1 = _coerce_xy(cmd.get("from", [0, 0]))
        layer = cmd.get("layer", "auto")
        margin = int(cmd.get("margin", 3) or 3)
        x1, y1 = snap_to_pad(x1, y1, net)

        with lock:
            before_state = capture_design_state()
            result = route_tap_by_name(_grid, net, x1, y1,
                                       layer_mode=layer, margin_override=margin)
            if result.success:
                segments = path_to_segments(_grid, result.path, net,
                                            _grid.layer_names,
                                            cmd.get("width", 0.25),
                                            start_mm=(x1, y1))
                state["tracks"].extend(segments)
                nid = _grid.get_net_id(net)
                for seg in segments:
                    layer_id = _grid.layer_ids.get(seg["layer"], 0)
                    w = max(1, int(round(seg["width"] / GRID_PITCH)))
                    _grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                                     w, layer_id, nid)
                via_positions = path_vias(result.path)
                for vx, vy in via_positions:
                    vx_mm, vy_mm = _grid.grid_to_mm(vx, vy)
                    state["vias"].append({"x": vx_mm, "y": vy_mm,
                                          "od": 0.6, "id": 0.3, "net": net})
                    _grid.mark_via(vx_mm, vy_mm, nid)
                state["version"] += 1
            after_state = capture_design_state()
            impact = compute_design_impact(before_state, after_state, net)

        resp = {
            "ok": result.success,
            "message": result.message,
            "length": result.length_mm,
            "vias": result.via_count,
            "segments": result.segment_count,
            "impact": impact,
        }
        if result.tap_point:
            resp["tap_point"] = result.tap_point
        self._json_response(resp)

    def _handle_unroute(self, cmd):
        _grid = rs.grid
        net = cmd.get("net", "")

        if net == "all":
            with lock:
                for t in state["tracks"]:
                    layer_id = _grid.layer_ids.get(t["layer"], 0)
                    nid = _grid.get_net_id(t["net"])
                    w = max(1, int(round(t.get("width", 0.25) / GRID_PITCH)))
                    gx1, gy1 = _grid.mm_to_grid(t["x1"], t["y1"])
                    gx2, gy2 = _grid.mm_to_grid(t["x2"], t["y2"])
                    hw = w // 2
                    for cx, cy in _line_cells(gx1, gy1, gx2, gy2):
                        for dy in range(-hw, hw + 1):
                            for dx in range(-hw, hw + 1):
                                _grid.clear_cell(layer_id, cx + dx, cy + dy, nid)
                for v in state["vias"]:
                    gx, gy = _grid.mm_to_grid(v["x"], v["y"])
                    r = _grid.via_od // 2
                    for layer in range(_grid.num_layers):
                        _grid.mark_circle(layer, gx, gy, r, 0)
                removed = len(state["tracks"])
                vias_removed = len(state["vias"])
                state["tracks"] = []
                state["vias"] = []
                state["version"] += 1
            self._json_response({"ok": True, "removed": removed,
                                  "vias_removed": vias_removed, "net": "all"})
            return

        with lock:
            before_state = capture_design_state()
            nid = _grid.get_net_id(net)
            kept = []
            removed = 0
            vias_removed = 0
            for t in state["tracks"]:
                if t["net"] == net:
                    layer_id = _grid.layer_ids.get(t["layer"], 0)
                    w = max(1, int(round(t.get("width", 0.25) / GRID_PITCH)))
                    gx1, gy1 = _grid.mm_to_grid(t["x1"], t["y1"])
                    gx2, gy2 = _grid.mm_to_grid(t["x2"], t["y2"])
                    hw = w // 2
                    for cx, cy in _line_cells(gx1, gy1, gx2, gy2):
                        for dy in range(-hw, hw + 1):
                            for dx in range(-hw, hw + 1):
                                _grid.clear_cell(layer_id, cx + dx, cy + dy, nid)
                    removed += 1
                else:
                    kept.append(t)
            state["tracks"] = kept
            kept_vias = []
            for v in state["vias"]:
                if v["net"] == net:
                    gx, gy = _grid.mm_to_grid(v["x"], v["y"])
                    r = _grid.via_od // 2
                    for layer in range(_grid.num_layers):
                        _grid.mark_circle(layer, gx, gy, r, 0)
                    vias_removed += 1
                else:
                    kept_vias.append(v)
            state["vias"] = kept_vias
            state["version"] += 1
            after_state = capture_design_state()
            impact = compute_design_impact(before_state, after_state, "")

        self._json_response({
            "ok": True,
            "removed": removed,
            "vias_removed": vias_removed,
            "net": net,
            "impact": impact,
        })

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        pass