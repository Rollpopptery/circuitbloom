"""
CircuitBloom — Component Layout Server (tree-based)

- Port 8080: serves the layout viewer to the browser
- Port 8081: accepts layout tree updates from the agent

The layout is a single JSON tree. Every node is either:
  - a leaf:  { "id": "U2", "w": 10, "h": 8 }
  - a group: { "id": "power", "arrange": "row", "children": [...] }

Size flows up. Position flows down. Arrangements within arrangements.
"""

import http.server
import json
import subprocess
import sys
import threading
import time

# ============================================================
# STATE
# ============================================================

state = {
    "version": 0,
    "tree": None,        # the layout tree (JSON-serialisable dict)
    "components": {},    # refdes -> {"shape": [w,h], "rotation": 0, "type": "..."}
}

lock = threading.Lock()


# ============================================================
# TREE OPERATIONS — surgical edits, no full rewrites
# ============================================================

def find_node(tree, node_id):
    """Find a node and its parent. Returns (node, parent, index) or (None, None, None)."""
    if tree["id"] == node_id:
        return tree, None, None
    return _find_recursive(tree, node_id)


def _find_recursive(parent, node_id):
    if "children" not in parent:
        return None, None, None
    for i, child in enumerate(parent["children"]):
        if child["id"] == node_id:
            return child, parent, i
        result = _find_recursive(child, node_id)
        if result[0] is not None:
            return result
    return None, None, None


def op_swap(tree, id_a, id_b):
    """Swap two nodes in the tree by id. Only touches those two nodes."""
    node_a, parent_a, idx_a = find_node(tree, id_a)
    node_b, parent_b, idx_b = find_node(tree, id_b)
    if node_a is None:
        return False, f"{id_a} not found"
    if node_b is None:
        return False, f"{id_b} not found"
    if parent_a is None or parent_b is None:
        return False, "cannot swap the root node"
    parent_a["children"][idx_a] = node_b
    parent_b["children"][idx_b] = node_a
    return True, f"swapped {id_a} <-> {id_b}"


def op_rotate(tree, node_id):
    """Rotate a leaf: swap its w and h."""
    node, _, _ = find_node(tree, node_id)
    if node is None:
        return False, f"{node_id} not found"
    if "w" not in node:
        return False, f"{node_id} is a group, not a leaf"
    node["w"], node["h"] = node["h"], node["w"]
    return True, f"rotated {node_id} -> {node['w']}x{node['h']}"

# ============================================================
# HTML PAGE
# ============================================================

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CircuitBloom</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    background: #fff;
    font-family: monospace;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    gap: 12px;
  }
  .title {
    font-size: 11px;
    color: #bbb;
    letter-spacing: 4px;
    text-transform: uppercase;
  }
  .title span { color: #999; }
  .toolbar {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  #exportBtn {
    font-family: monospace;
    font-size: 11px;
    padding: 6px 16px;
    cursor: pointer;
    border: 1px solid #999;
    background: #fff;
    letter-spacing: 1px;
    color: #666;
  }
  #exportBtn:hover { background: #f5f5f5; border-color: #666; color: #000; }
  #exportBtn:disabled { opacity: 0.3; cursor: wait; }
  #exportStatus { font-size: 9px; color: #999; }
  .harness {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 90vw;
    height: 80vh;
  }
  #board {
    position: relative;
    transform-origin: center center;
  }
  .leaf {
    position: absolute;
    border: 1px solid #000;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: monospace;
    font-size: 21px;
    background: #f5f5f0;
  }
  .group {
    position: absolute;
    border: 1px dashed #ccc;
  }
  .group-label {
    position: absolute;
    top: -16px;
    left: 2px;
    font-size: 11px;
    color: #999;
    background: #fff;
    padding: 0 3px;
    font-family: monospace;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
</style>
</head>
<body>

<div class="title">component layout <span>circuitbloom</span></div>

<div class="harness" id="harness">
  <div id="board"></div>
</div>

<div class="toolbar">
  <button id="exportBtn" onclick="doExport()">Update KiCad</button>
  <span id="exportStatus"></span>
</div>

<script>
var CELL = 16;
var GAP = 1;
var tree = __TREE__;
var components = __COMPONENTS__;

function computeSize(node) {
  if (node.w !== undefined) {
    node._w = node.w * CELL;
    node._h = node.h * CELL;
    return;
  }
  if (!node.children || node.children.length === 0) {
    node._w = 0; node._h = 0; return;
  }
  node.children.forEach(computeSize);
  var isRow = node.arrange === 'row';
  var main = 0, cross = 0;
  node.children.forEach(function(c, i) {
    main += isRow ? c._w : c._h;
    if (i > 0) main += GAP;
    cross = Math.max(cross, isRow ? c._h : c._w);
  });
  node._w = isRow ? main : cross;
  node._h = isRow ? cross : main;
}

function computePositions(node, x, y) {
  node._x = x; node._y = y;
  if (!node.children) return;
  var isRow = node.arrange === 'row';
  var cursor = 0;
  node.children.forEach(function(c, i) {
    if (i > 0) cursor += GAP;
    computePositions(c, isRow ? x + cursor : x, isRow ? y : y + cursor);
    cursor += isRow ? c._w : c._h;
  });
}

function render(tree) {
  var board = document.getElementById('board');
  board.innerHTML = '';
  if (!tree) return;
  computeSize(tree);
  computePositions(tree, 0, 0);
  board.style.width = tree._w + 'px';
  board.style.height = tree._h + 'px';

  function walk(node) {
    if (node.w !== undefined) {
      var el = document.createElement('div');
      el.className = 'leaf';
      el.style.left = node._x + 'px';
      el.style.top = node._y + 'px';
      el.style.width = node._w + 'px';
      el.style.height = node._h + 'px';
      el.textContent = node.id;
      var meta = components[node.id];
      if (meta) el.title = node.id + ' — ' + meta.type;
      board.appendChild(el);
    }
    if (node.children) {
      if (node.id !== tree.id) {
        var g = document.createElement('div');
        g.className = 'group';
        g.style.left = node._x + 'px';
        g.style.top = node._y + 'px';
        g.style.width = node._w + 'px';
        g.style.height = node._h + 'px';
        var lbl = document.createElement('span');
        lbl.className = 'group-label';
        lbl.textContent = node.id;
        g.appendChild(lbl);
        board.appendChild(g);
      }
      node.children.forEach(walk);
    }
  }
  walk(tree);
  scaleBoard();
}

function scaleBoard() {
  var harness = document.getElementById('harness');
  var board = document.getElementById('board');
  if (!board || !harness) return;
  board.style.transform = 'none';
  var bw = board.offsetWidth;
  var bh = board.offsetHeight;
  var hw = harness.clientWidth;
  var hh = harness.clientHeight;
  if (bw === 0 || bh === 0) return;
  var scale = Math.min(hw / bw, hh / bh, 3);
  board.style.transform = 'scale(' + scale + ')';
}

render(tree);
window.addEventListener('resize', function() { render(tree); });

async function doExport() {
  var btn = document.getElementById('exportBtn');
  var status = document.getElementById('exportStatus');
  btn.disabled = true;
  btn.textContent = 'Exporting...';
  status.textContent = '';
  try {
    var r = await fetch('/export', { method: 'POST' });
    var d = await r.json();
    if (d.ok) {
      status.textContent = d.message || 'Done.';
      status.style.color = '#090';
    } else {
      status.textContent = d.error || 'Failed.';
      status.style.color = '#c00';
    }
  } catch(e) {
    status.textContent = 'Server error.';
    status.style.color = '#c00';
  }
  btn.disabled = false;
  btn.textContent = 'Update KiCad';
}

var v = __VERSION__;
setInterval(async function() {
  try {
    var r = await fetch('/version');
    var d = await r.json();
    if (d.v > v) location.reload();
  } catch(e) {}
}, 400);
</script>

</body>
</html>"""


def build_page():
    with lock:
        tree_json = json.dumps(state["tree"]) if state["tree"] else "null"
        comp_json = json.dumps(state["components"])
        ver = state["version"]

    page = PAGE.replace("__TREE__", tree_json)
    page = page.replace("__COMPONENTS__", comp_json)
    page = page.replace("__VERSION__", str(ver))
    return page


# ============================================================
# BROWSER — port 8080
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
        if self.path == '/export':
            self._handle_export()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_export(self):
        print("  [export] Update KiCad triggered...")
        with lock:
            state_json = json.dumps(state)
        try:
            result = subprocess.run(
                [sys.executable, 'export_kicad.py'],
                input=state_json,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                msg = result.stdout.strip() or "KiCad updated."
                print(f"  [export] OK: {msg}")
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "message": msg}).encode())
            else:
                err = result.stderr.strip() or "Unknown error."
                print(f"  [export] FAIL: {err}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": err}).encode())
        except FileNotFoundError:
            msg = "export_kicad.py not found."
            print(f"  [export] {msg}")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())
        except subprocess.TimeoutExpired:
            msg = "Export timed out."
            print(f"  [export] {msg}")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())

    def log_message(self, format, *args):
        pass


# ============================================================
# AGENT — port 8081
# ============================================================

class AgentHandler(http.server.BaseHTTPRequestHandler):
    """
    Agent POSTs JSON with any combination of:

      tree:        the full layout tree (replaces current tree)
      components:  { "R1": {"shape": [5,2], "rotation": 0, "type": "0603_R"}, ... }
      rotate:      { "J2": 90 }  — shorthand to set component metadata rotation
      swap:        ["R1", "R6"] — swap two nodes in the tree
      rotate_leaf: "J2"         — swap w and h of a leaf in the tree

    Partial updates OK — omit keys you don't want to change.
    """

    def do_POST(self):
        length = int(self.headers['Content-Length'])
        body = self.rfile.read(length)

        try:
            update = json.loads(body)
            messages = []
            with lock:
                if "tree" in update:
                    state["tree"] = update["tree"]
                if "components" in update:
                    for ref, data in update["components"].items():
                        state["components"][ref] = data
                if "rotate" in update:
                    for ref, angle in update["rotate"].items():
                        if ref not in state["components"]:
                            state["components"][ref] = {}
                        old_angle = state["components"][ref].get("rotation", 0)
                        diff = (angle - old_angle) % 360
                        state["components"][ref]["rotation"] = angle
                        # Auto-swap dimensions for 90°/270° changes
                        if diff in (90, 270) and state["tree"] is not None:
                            ok, msg = op_rotate(state["tree"], ref)
                            if ok:
                                messages.append(msg)
                if "set_rotation" in update:
                    for ref, angle in update["set_rotation"].items():
                        if ref not in state["components"]:
                            state["components"][ref] = {}
                        state["components"][ref]["rotation"] = angle
                if "swap" in update:
                    if state["tree"] is None:
                        messages.append("swap failed: no tree loaded")
                    else:
                        ids = update["swap"]
                        ok, msg = op_swap(state["tree"], ids[0], ids[1])
                        messages.append(msg)
                        if not ok:
                            self.send_response(400)
                            self.send_header('Content-Type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())
                            return
                if "rotate_leaf" in update:
                    if state["tree"] is None:
                        messages.append("rotate_leaf failed: no tree loaded")
                    else:
                        ref = update["rotate_leaf"]
                        ok, msg = op_rotate(state["tree"], ref)
                        messages.append(msg)
                        if not ok:
                            self.send_response(400)
                            self.send_header('Content-Type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())
                            return
                state["version"] += 1
                v = state["version"]

            keys = list(update.keys())
            if "components" in update:
                keys.remove("components")
                keys.extend(f"comp:{n}" for n in update["components"])
            if "rotate" in update:
                keys.remove("rotate")
                keys.extend(f"rotate:{n}" for n in update["rotate"])
            if messages:
                keys.extend(messages)
            print(f"  [v{v}] Updated: {', '.join(keys)}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "v": v}).encode())

        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):
        """
        GET /              — full state (tree + components)
        GET /tree          — layout tree only
        GET /components    — component table only
        GET /component/R1  — single component
        """
        if self.path == '/tree':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps(state["tree"], indent=2).encode())
        elif self.path == '/components':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps(state["components"], indent=2).encode())
        elif self.path.startswith('/component/'):
            ref = self.path.split('/')[-1]
            with lock:
                comp = state["components"].get(ref)
            if comp:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(comp, indent=2).encode())
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"{ref} not found"}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps(state, indent=2).encode())

    def log_message(self, format, *args):
        pass


# ============================================================
# RUN
# ============================================================

def run():
    browser = http.server.HTTPServer(('0.0.0.0', 8080), BrowserHandler)
    agent = http.server.HTTPServer(('0.0.0.0', 8081), AgentHandler)

    threading.Thread(target=browser.serve_forever, daemon=True).start()
    threading.Thread(target=agent.serve_forever, daemon=True).start()

    print()
    print("  CircuitBloom — Component Layout Server (tree)")
    print("  ==============================================")
    print("  Browser:  http://localhost:8080")
    print("  Agent:    http://localhost:8081")
    print()
    print("  Operations:")
    print('    tree:        POST { "tree": {...} }           — replace full tree')
    print('    swap:        POST { "swap": ["R1","R6"] }     — swap two nodes')
    print('    rotate_leaf: POST { "rotate_leaf": "J2" }     — swap w/h of a leaf')
    print('    components:  POST { "components": {...} }     — update metadata')
    print()
    print("  Waiting for agent...")
    print("  Press Ctrl+C to stop.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopped.")
        browser.shutdown()
        agent.shutdown()


if __name__ == '__main__':
    run()