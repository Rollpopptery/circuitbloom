"""HTTP request handlers for browser (8083) and agent (8084) ports."""

import http.server
import json
import os

from dpcb_router import route_by_name, route_tap_by_name, LAYER_NAMES, GRID_PITCH, _line_cells
from dpcb_router8 import route8_by_name
from route_convert import path_to_segments, path_vias
import route_state as rs
from route_state import state, lock, reload_bloom, reload_from_kicad, save_bloom, push_to_kicad, snap_to_pad, get_transitions, get_orphan_vias, get_clearance, get_density, get_nearest_track, handle_move, handle_place, handle_rotate, handle_place_via, handle_add_track, handle_delete_tracks, handle_delete_via, handle_highlight, handle_mark, handle_clear_marks, handle_set_footprint, get_footprints, capture_design_state, compute_design_impact, run_drc, find_via_spot

_VIEWER_HTML = None


def _load_viewer_html():
    global _VIEWER_HTML
    if _VIEWER_HTML is None:
        html_path = os.path.join(os.path.dirname(__file__), "viewer.html")
        with open(html_path) as f:
            _VIEWER_HTML = f.read()
    return _VIEWER_HTML


def build_page():
    with lock:
        pads_json = json.dumps(state["pads"])
        tracks_json = json.dumps(state["tracks"])
        vias_json = json.dumps(state["vias"])
        board_json = json.dumps(state["board"])
        nets_json = json.dumps(state["nets"])
        comps_json = json.dumps(state["components"])
        rects_json = json.dumps(state["rects"])
        highlight_json = json.dumps(state["highlight"])
        markers_json = json.dumps(state["markers"])
        heatmap_json = json.dumps(state.get("heatmap"))
        ver = state["version"]

    page = _load_viewer_html()
    page = page.replace("__PADS__", pads_json)
    page = page.replace("__TRACKS__", tracks_json)
    page = page.replace("__VIAS__", vias_json)
    page = page.replace("__BOARD__", board_json)
    page = page.replace("__NETS__", nets_json)
    page = page.replace("__COMPS__", comps_json)
    page = page.replace("__RECTS__", rects_json)
    page = page.replace("__HIGHLIGHT__", highlight_json)
    page = page.replace("__MARKERS__", markers_json)
    page = page.replace("__HEATMAP__", heatmap_json)
    page = page.replace("__VERSION__", str(ver))
    return page


# ============================================================
# BROWSER — port 8083
# ============================================================

class BrowserHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/version':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps({"v": state["version"]}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(build_page().encode())

    def do_POST(self):
        if self.path == '/api':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                cmd = json.loads(body) if body else {}
            except json.JSONDecodeError as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
                return
            action = cmd.get("action")
            if action == "move":
                handle_move(cmd)
                self._json_response({"ok": True})
            elif action == "rotate":
                handle_rotate(cmd)
                self._json_response({"ok": True})
            else:
                self._json_response({"ok": False, "error": "unknown action"}, 400)
        else:
            self.send_response(404)
            self.end_headers()

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        pass


# ============================================================
# AGENT — port 8084
# ============================================================

class AgentHandler(http.server.BaseHTTPRequestHandler):
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
        elif self.path == '/status':
            _grid = rs.grid  # noqa: fresh lookup below
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
            # Optional socket_path param: /capture_kicad?socket=ipc:///tmp/kicad/api-41011.sock
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
            tol = 0.15
            self._json_response(get_transitions(tol))
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
        elif action == "drc":
            ok, result = run_drc(cmd)
            self._json_response({"ok": ok, **result})
        else:
            self._json_response({"ok": False, "error": f"unknown action: {action}"}, 400)

    def _handle_route(self, cmd):
        _grid = rs.grid  # noqa: fresh lookup below
        net = cmd.get("net", "")
        x1, y1 = cmd.get("from", [0, 0])
        x2, y2 = cmd.get("to", [0, 0])
        layer = cmd.get("layer", "auto")
        margin = cmd.get("margin", 3)
        use8 = cmd.get("use8", False)

        # Snap endpoints to actual pad positions
        x1, y1 = snap_to_pad(x1, y1, net)
        x2, y2 = snap_to_pad(x2, y2, net)

        with lock:
            # Capture design state BEFORE routing
            before_state = capture_design_state()

            if use8:
                result = route8_by_name(_grid, net, x1, y1, x2, y2,
                                        layer_mode=layer, margin_override=margin)
            else:
                result = route_by_name(_grid, net, x1, y1, x2, y2,
                                       layer_mode=layer, margin_override=margin)
            if result.success:
                segments = path_to_segments(_grid, result.path, net,
                                            LAYER_NAMES,
                                            cmd.get("width", 0.25),
                                            start_mm=(x1, y1),
                                            end_mm=(x2, y2))
                state["tracks"].extend(segments)
                nid = _grid.get_net_id(net)
                for seg in segments:
                    layer_id = 0 if seg["layer"] == "F.Cu" else 1
                    w = max(1, int(round(seg["width"] / GRID_PITCH)))
                    _grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                                     w, layer_id, nid)
                via_positions = path_vias(result.path)
                for vx, vy in via_positions:
                    vx_mm, vy_mm = _grid.grid_to_mm(vx, vy)
                    state["vias"].append({
                        "x": vx_mm, "y": vy_mm,
                        "od": 0.6, "id": 0.3, "net": net
                    })
                    _grid.mark_via(vx_mm, vy_mm, nid)

                state["version"] += 1

            # Capture design state AFTER routing
            after_state = capture_design_state()

            # Compute design impact
            impact = compute_design_impact(before_state, after_state, net)

        resp = {
            "ok": result.success,
            "message": result.message,
            "length": result.length_mm,
            "vias": result.via_count,
            "segments": result.segment_count,
            "impact": impact
        }
        self._json_response(resp)

    def _handle_route_tap(self, cmd):
        _grid = rs.grid  # noqa: fresh lookup below
        net = cmd.get("net", "")
        x1, y1 = cmd.get("from", [0, 0])
        layer = cmd.get("layer", "auto")
        margin = cmd.get("margin", 3)

        x1, y1 = snap_to_pad(x1, y1, net)

        with lock:
            # Capture design state BEFORE routing
            before_state = capture_design_state()

            result = route_tap_by_name(_grid, net, x1, y1,
                                       layer_mode=layer, margin_override=margin)
            if result.success:
                segments = path_to_segments(_grid, result.path, net,
                                            LAYER_NAMES,
                                            cmd.get("width", 0.25),
                                            start_mm=(x1, y1))
                state["tracks"].extend(segments)
                nid = _grid.get_net_id(net)
                for seg in segments:
                    layer_id = 0 if seg["layer"] == "F.Cu" else 1
                    w = max(1, int(round(seg["width"] / GRID_PITCH)))
                    _grid.mark_track(seg["x1"], seg["y1"], seg["x2"], seg["y2"],
                                     w, layer_id, nid)
                via_positions = path_vias(result.path)
                for vx, vy in via_positions:
                    vx_mm, vy_mm = _grid.grid_to_mm(vx, vy)
                    state["vias"].append({
                        "x": vx_mm, "y": vy_mm,
                        "od": 0.6, "id": 0.3, "net": net
                    })
                    _grid.mark_via(vx_mm, vy_mm, nid)
                state["version"] += 1

            # Capture design state AFTER routing
            after_state = capture_design_state()

            # Compute design impact
            impact = compute_design_impact(before_state, after_state, net)

        resp = {
            "ok": result.success,
            "message": result.message,
            "length": result.length_mm,
            "vias": result.via_count,
            "segments": result.segment_count,
            "impact": impact
        }
        if result.tap_point:
            resp["tap_point"] = result.tap_point
        self._json_response(resp)

    def _handle_unroute(self, cmd):
        _grid = rs.grid  # noqa: fresh lookup below
        net = cmd.get("net", "")
        with lock:
            # Capture design state BEFORE unrouting
            before_state = capture_design_state()

            nid = _grid.get_net_id(net)
            kept = []
            removed = 0
            vias_removed = 0
            for t in state["tracks"]:
                if t["net"] == net:
                    layer_id = 0 if t["layer"] == "F.Cu" else 1
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
                    for layer in (0, 1):
                        _grid.mark_circle(layer, gx, gy, r, 0)
                    vias_removed += 1
                else:
                    kept_vias.append(v)
            state["vias"] = kept_vias

            state["version"] += 1

            # Capture design state AFTER unrouting
            after_state = capture_design_state()

            # Compute design impact (net is now gone, so pass empty string)
            impact = compute_design_impact(before_state, after_state, "")

        self._json_response({
            "ok": True,
            "removed": removed,
            "vias_removed": vias_removed,
            "net": net,
            "impact": impact
        })

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        pass
