#!/usr/bin/env python3
"""
MCP wrapper for route_server.py
Thin translation layer — no business logic.
Exposes route server HTTP API (port 8084) as MCP tools for Claude Code.
"""

import json
import sys
import urllib.request
import urllib.error
from typing import Any

ROUTE_SERVER = "http://localhost:8084"

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_get(path: str) -> Any:
    url = f"{ROUTE_SERVER}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        return {"error": str(e)}

def http_post(body: dict) -> Any:
    url = f"{ROUTE_SERVER}/"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        return {"error": str(e)}

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_status",
        "description": "Grid stats, track/via/pad/net counts. Use first to understand current board state.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_pads",
        "description": "All pads: ref, pin, net, x/y in mm, SMD flag.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_pads_for_net",
        "description": "All pads belonging to a specific net.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net": {"type": "string", "description": "Net name e.g. GND, VCC_3V3"}
            },
            "required": ["net"]
        }
    },
    {
        "name": "get_nets",
        "description": "All nets and their viewer colors.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_placement",
        "description": "All component placements as grid-cell coordinates {ref: {col, row, w, h}}.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_density",
        "description": "10mm sector density map — F.Cu/B.Cu occupancy percent per sector. Use to find clear routing corridors before routing.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_clearance",
        "description": "Per-net minimum clearance to nearest foreign obstacle, sorted worst-first. Use to audit trace quality.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_orphan_vias",
        "description": "Vias not connected to any trace endpoint. Check after routing.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_transitions",
        "description": "Layer transition points. Flags missing vias at layer changes.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_vias",
        "description": "All vias on the board.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_nearest_track",
        "description": "Closest point on any trace of a net to a given x/y. Use for T-junction routing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net": {"type": "string",  "description": "Net name"},
                "x":   {"type": "number",  "description": "X in mm"},
                "y":   {"type": "number",  "description": "Y in mm"}
            },
            "required": ["net", "x", "y"]
        }
    },
    {
        "name": "get_footprints",
        "description": "All footprint mappings {package: {kicad_mod, pads}}.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "route",
        "description": (
            "Route a trace between two points for a net. "
            "Endpoints snap to nearest pad on the net. "
            "Default margin=3 (0.3mm clearance), reduce to 2 for tight areas. "
            "Use use8=true for 45-degree traces after fan-out stubs. "
            "Use layer=auto to allow vias and layer changes. "
            "Response includes clearance delta for all nets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":    {"type": "string",  "description": "Net name"},
                "from":   {"type": "array",   "items": {"type": "number"}, "description": "[x, y] in mm"},
                "to":     {"type": "array",   "items": {"type": "number"}, "description": "[x, y] in mm"},
                "layer":  {"type": "string",  "description": "F.Cu, B.Cu, or auto", "default": "auto"},
                "margin": {"type": "integer", "description": "Clearance in grid cells (1=0.1mm, 2=0.2mm, 3=0.3mm)", "default": 3},
                "use8":   {"type": "boolean", "description": "8-direction 45-degree routing", "default": True},
                "width":  {"type": "number",  "description": "Track width in mm", "default": 0.25}
            },
            "required": ["net", "from", "to"]
        }
    },
    {
        "name": "route_tap",
        "description": (
            "Route from a pad to the nearest existing trace on its net. "
            "Use for T-junctions and power taps. "
            "Response includes clearance delta for all nets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":    {"type": "string",  "description": "Net name"},
                "from":   {"type": "array",   "items": {"type": "number"}, "description": "[x, y] in mm"},
                "margin": {"type": "integer", "description": "Clearance in grid cells (1=0.1mm, 2=0.2mm, 3=0.3mm)", "default": 3}
            },
            "required": ["net", "from"]
        }
    },
    {
        "name": "unroute",
        "description": (
            "Remove all tracks and vias for a net. "
            "Response includes clearance delta showing corridors freed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "net": {"type": "string", "description": "Net name to unroute"}
            },
            "required": ["net"]
        }
    },
    {
        "name": "find_via_spot",
        "description": (
            "BFS from a pad to find the nearest reachable spot where a via fits. "
            "Flood-fills on the pad's layer through empty/same-net cells, then checks "
            "via footprint clear on both layers. Guarantees the path from pad to via is clear."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":    {"type": "string", "description": "Net name"},
                "x":      {"type": "number", "description": "Pad X in mm"},
                "y":      {"type": "number", "description": "Pad Y in mm"},
                "margin":     {"type": "integer", "description": "Clearance cells for via check (default 3)", "default": 3},
                "min_radius": {"type": "integer", "description": "Min distance in grid cells from pad (default 10 = 1mm)", "default": 10},
                "max_radius": {"type": "integer", "description": "Max search distance in grid cells (default 50 = 5mm)", "default": 50}
            },
            "required": ["net", "x", "y"]
        }
    },
    {
        "name": "add_track",
        "description": "Add a single track segment manually. Use for precise control over individual segments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":   {"type": "string", "description": "Net name"},
                "x1":    {"type": "number", "description": "Start X in mm"},
                "y1":    {"type": "number", "description": "Start Y in mm"},
                "x2":    {"type": "number", "description": "End X in mm"},
                "y2":    {"type": "number", "description": "End Y in mm"},
                "layer": {"type": "string", "description": "F.Cu or B.Cu", "default": "F.Cu"},
                "width": {"type": "number", "description": "Track width in mm", "default": 0.25}
            },
            "required": ["net", "x1", "y1", "x2", "y2"]
        }
    },
    {
        "name": "delete_tracks",
        "description": "Delete track segments where both endpoints fall within a bounding box. Net filter is optional.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":   {"type": "string", "description": "Net name (optional, omit to match all)"},
                "x_min": {"type": "number", "description": "Bounding box min X"},
                "y_min": {"type": "number", "description": "Bounding box min Y"},
                "x_max": {"type": "number", "description": "Bounding box max X"},
                "y_max": {"type": "number", "description": "Bounding box max Y"}
            },
            "required": ["x_min", "y_min", "x_max", "y_max"]
        }
    },
    {
        "name": "delete_via",
        "description": "Delete vias within a bounding box. Net filter is optional.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net":   {"type": "string", "description": "Net name (optional, omit to match all)"},
                "x_min": {"type": "number", "description": "Bounding box min X"},
                "y_min": {"type": "number", "description": "Bounding box min Y"},
                "x_max": {"type": "number", "description": "Bounding box max X"},
                "y_max": {"type": "number", "description": "Bounding box max Y"}
            },
            "required": ["x_min", "y_min", "x_max", "y_max"]
        }
    },
    {
        "name": "drc",
        "description": (
            "Run KiCad Design Rule Check on the board file. "
            "Board path is auto-detected from capture_kicad. "
            "Returns violations with positions and severity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity":         {"type": "string", "description": "all, error, warning, or exclusions", "default": "all"},
                "all_track_errors": {"type": "boolean", "description": "Report all errors per track", "default": False},
                "schematic_parity": {"type": "boolean", "description": "Include schematic parity check", "default": False},
                "refill_zones":     {"type": "boolean", "description": "Refill zones before DRC", "default": False}
            }
        }
    },
    {
        "name": "place_via",
        "description": "Place a via at a specific point. Check get_orphan_vias afterwards.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net": {"type": "string", "description": "Net name"},
                "x":   {"type": "number", "description": "X in mm"},
                "y":   {"type": "number", "description": "Y in mm"}
            },
            "required": ["net", "x", "y"]
        }
    },
    {
        "name": "move_component",
        "description": "Move component by relative grid-cell delta. Call save then reload before routing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",  "description": "Component reference e.g. R1, U4"},
                "dw":  {"type": "integer", "description": "Column delta"},
                "dh":  {"type": "integer", "description": "Row delta"}
            },
            "required": ["ref", "dw", "dh"]
        }
    },
    {
        "name": "place_component",
        "description": "Place component at absolute grid-cell coordinates. Call save then reload before routing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",  "description": "Component reference"},
                "col": {"type": "integer", "description": "Column grid cell"},
                "row": {"type": "integer", "description": "Row grid cell"}
            },
            "required": ["ref", "col", "row"]
        }
    },
    {
        "name": "rotate_component",
        "description": "Rotate component 90 degrees clockwise. Call save then reload before routing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Component reference"}
            },
            "required": ["ref"]
        }
    },
    {
        "name": "set_footprint",
        "description": "Set KiCad footprint path for a package. Changes are in-memory until save.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package":   {"type": "string", "description": "Package name e.g. 0805"},
                "kicad_mod": {"type": "string", "description": "Path relative to KiCad footprints folder"}
            },
            "required": ["package", "kicad_mod"]
        }
    },
    {
        "name": "save",
        "description": "Save current tracks, vias and placement to bloom file.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "reload",
        "description": "Reload bloom file and rebuild grid. Must call after move/rotate/place before routing.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "mark",
        "description": "Place a colored annotation marker in the viewer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x":     {"type": "number", "description": "X in mm"},
                "y":     {"type": "number", "description": "Y in mm"},
                "color": {"type": "string", "description": "Hex color e.g. #ff0000"},
                "label": {"type": "string", "description": "Text label"},
                "size":  {"type": "number", "description": "Scale factor, 1=normal", "default": 1},
                "lx":    {"type": "number", "description": "Line endpoint X in mm (optional)"},
                "ly":    {"type": "number", "description": "Line endpoint Y in mm (optional)"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "clear_marks",
        "description": "Remove all annotation markers from the viewer.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "highlight",
        "description": "Highlight a net in the viewer. Pass empty string to clear.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "net": {"type": "string", "description": "Net name, or empty string to clear"}
            },
            "required": ["net"]
        }
    },
    {
        "name": "capture_kicad",
        "description": "Capture board state from running KiCad PCB editor. Populates pads, tracks, vias, nets, and copper heatmap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "socket": {"type": "string", "description": "IPC socket path (auto-detects if omitted)"}
            }
        }
    },
    {
        "name": "save_kicad",
        "description": "Save the KiCad board file to disk. Call after confirmed route changes — IPC edits are in-memory until saved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "socket": {"type": "string", "description": "IPC socket path (auto-detects if omitted)"}
            }
        }
    },
    {
        "name": "push_kicad",
        "description": "Push all tracks and vias from server state to KiCad.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "socket": {"type": "string", "description": "IPC socket path (auto-detects if omitted)"}
            }
        }
    },
]

# ── Tool dispatch ─────────────────────────────────────────────────────────────

def call_tool(name: str, args: dict) -> Any:
    # GET endpoints
    if name == "get_status":        return http_get("/status")
    if name == "get_pads":          return http_get("/pads")
    if name == "get_pads_for_net":  return http_get(f"/pads/{args['net']}")
    if name == "get_nets":          return http_get("/nets")
    if name == "get_placement":     return http_get("/placement")
    if name == "get_density":       return http_get("/density")
    if name == "get_clearance":     return http_get("/clearance")
    if name == "get_orphan_vias":   return http_get("/orphan_vias")
    if name == "get_transitions":   return http_get("/get_transitions")
    if name == "get_vias":          return http_get("/get_vias")
    if name == "get_footprints":    return http_get("/footprints")
    if name == "get_nearest_track":
        return http_get(
            f"/nearest_track?net={args['net']}&x={args['x']}&y={args['y']}"
        )

    # POST endpoints
    if name == "route":
        return http_post({
            "action": "route",
            "net":    args["net"],
            "from":   args["from"],
            "to":     args["to"],
            "layer":  args.get("layer", "auto"),
            "margin": args.get("margin", 3),
            "use8":   args.get("use8", True),
            "width":  args.get("width", 0.25),
        })
    if name == "route_tap":
        return http_post({
            "action": "route_tap",
            "net":    args["net"],
            "from":   args["from"],
            "margin": args.get("margin", 3),
        })
    if name == "unroute":
        return http_post({"action": "unroute", "net": args["net"]})
    if name == "find_via_spot":
        url = f"/find_via_spot?net={args['net']}&x={args['x']}&y={args['y']}"
        if 'margin' in args: url += f"&margin={args['margin']}"
        if 'min_radius' in args: url += f"&min_radius={args['min_radius']}"
        if 'max_radius' in args: url += f"&max_radius={args['max_radius']}"
        return http_get(url)
    if name == "add_track":
        return http_post({
            "action": "add_track",
            "net":    args["net"],
            "x1":     args["x1"],
            "y1":     args["y1"],
            "x2":     args["x2"],
            "y2":     args["y2"],
            "layer":  args.get("layer", "F.Cu"),
            "width":  args.get("width", 0.25),
        })
    if name == "delete_tracks":
        body = {
            "action": "delete_tracks",
            "x_min":  args["x_min"],
            "y_min":  args["y_min"],
            "x_max":  args["x_max"],
            "y_max":  args["y_max"],
        }
        if "net" in args:
            body["net"] = args["net"]
        return http_post(body)
    if name == "delete_via":
        body = {
            "action": "delete_via",
            "x_min":  args["x_min"],
            "y_min":  args["y_min"],
            "x_max":  args["x_max"],
            "y_max":  args["y_max"],
        }
        if "net" in args:
            body["net"] = args["net"]
        return http_post(body)
    if name == "drc":
        body = {"action": "drc"}
        for k in ("severity", "all_track_errors", "schematic_parity", "refill_zones"):
            if k in args:
                body[k] = args[k]
        return http_post(body)
    if name == "place_via":
        return http_post({
            "action": "place_via",
            "net":    args["net"],
            "x":      args["x"],
            "y":      args["y"],
        })
    if name == "move_component":
        return http_post({
            "action": "move",
            "ref":    args["ref"],
            "dw":     args["dw"],
            "dh":     args["dh"],
        })
    if name == "place_component":
        return http_post({
            "action": "place",
            "ref":    args["ref"],
            "col":    args["col"],
            "row":    args["row"],
        })
    if name == "rotate_component":
        return http_post({"action": "rotate", "ref": args["ref"]})
    if name == "set_footprint":
        return http_post({
            "action":    "set_footprint",
            "package":   args["package"],
            "kicad_mod": args["kicad_mod"],
        })
    if name == "save":
        return http_post({"action": "save"})
    if name == "reload":
        return http_post({"action": "reload"})
    if name == "mark":
        body = {"action": "mark", "x": args["x"], "y": args["y"]}
        for k in ("color", "label", "size", "lx", "ly"):
            if k in args:
                body[k] = args[k]
        return http_post(body)
    if name == "clear_marks":
        return http_post({"action": "clear_marks"})
    if name == "highlight":
        return http_post({"action": "highlight", "net": args["net"]})
    if name == "capture_kicad":
        body = {"action": "capture_kicad"}
        if "socket" in args:
            body["socket"] = args["socket"]
        return http_post(body)
    if name == "save_kicad":
        try:
            from kipy import KiCad
            socket = args.get("socket", "ipc:///tmp/kicad/api.sock")
            kicad_inst = KiCad(socket_path=socket)
            board = kicad_inst.get_board()
            board.save()
            return {"ok": True, "message": "Board saved"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if name == "push_kicad":
        body = {"action": "push_kicad"}
        if "socket" in args:
            body["socket"] = args["socket"]
        return http_post(body)

    return {"error": f"Unknown tool: {name}"}

# ── MCP protocol over stdio ───────────────────────────────────────────────────

def send(msg: dict):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

def handle(msg: dict):
    method = msg.get("method")
    mid    = msg.get("id")

    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "route-server-mcp", "version": "1.0.0"}
        }})

    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})

    elif method == "tools/call":
        name   = msg["params"]["name"]
        args   = msg["params"].get("arguments", {})
        result = call_tool(name, args)
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
        }})

    elif method == "notifications/initialized":
        pass

    else:
        if mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }})

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            handle(msg)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")

if __name__ == "__main__":
    main()