"""Browser handler for port 8083."""
import http.server
import json
import os

from route_state import state, lock, sse_subscribe, sse_unsubscribe, handle_move, handle_rotate


def _load_viewer_html():
    html_path = os.path.join(os.path.dirname(__file__), "viewer.html")
    with open(html_path) as f:
        return f.read()


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


class BrowserHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            sse_subscribe(self.wfile)
            try:
                import time
                while True:
                    time.sleep(1)
            except Exception:
                pass
            finally:
                sse_unsubscribe(self.wfile)
            return
        elif self.path == '/version':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps({"v": state["version"]}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-store')
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