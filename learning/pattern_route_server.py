#!/usr/bin/env python3
"""
pattern_route_server.py — HTTP server for pattern-based trace retrieval.

Loads DINOv2 and ChromaDB collection at startup. Serves queries for
candidate traces based on full pattern matching (pads + existing traces).

Depends on route_server running on port 8084 for board state.

Port 8085: Pattern query API

Endpoints:
    GET  /query?from=XU1.18&to=U4.12&net=SPI_SCK&n=5
         Returns candidate traces as JSON

    GET  /render?from=XU1.18&to=U4.12&net=SPI_SCK&n=5&width=800
         Returns candidate overlay image as PNG

    GET  /status
         Collection size, model info

    GET  /nets
         List nets with routable pad pairs from current board

Usage:
    python3 pattern_route_server.py [--port 8085] [--db path] [--route-server http://localhost:8084]
"""

import argparse
import http.server
import io
import json
import math
import os
import sys
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from pad_pattern_render import (
    count_component_pins,
    count_visible_obstacles,
    choose_source_dest,
)
from trace_patterndb import search_patterns, encode_image, _load_dino
from trace_transform import retrieve_and_transform

import chromadb

# ============================================================
# GLOBALS — loaded once at startup
# ============================================================

_collection = None
_db_path = None
_route_server = None


def _init(db_path, route_server):
    """Load DINOv2 and open ChromaDB collection."""
    global _collection, _db_path, _route_server

    _db_path = db_path
    _route_server = route_server

    print("Loading DINOv2...")
    _load_dino()

    print(f"Opening collection: {db_path}")
    client = chromadb.PersistentClient(path=db_path)
    _collection = client.get_collection("trace_patterns")
    print(f"Collection: {_collection.count()} patterns")
    print()


def _fetch_board_state():
    """Fetch current board state from route server."""
    data = json.loads(urllib.request.urlopen(_route_server + "/").read())
    return data


def _find_pad(pads, ref_pin):
    """Find a pad by REF.PIN string."""
    parts = ref_pin.split(".", 1)
    if len(parts) != 2:
        return None
    ref, pin = parts
    for p in pads:
        if p["ref"] == ref and p["pin"] == pin:
            return p
    return None


def _get_target_layers():
    """Get target board's layer mapping from route server."""
    try:
        status = json.loads(urllib.request.urlopen(_route_server + "/status").read())
        layer_names = status.get("layer_names", {})
        return {int(idx): name for idx, name in layer_names.items()}
    except Exception:
        return {0: "F.Cu", 1: "B.Cu"}


def _query_candidates(from_pad_str, to_pad_str, net, n=5):
    """Core query logic. Returns (candidates, query_info, error)."""
    data = _fetch_board_state()
    pads = data.get("pads", [])
    tracks = data.get("tracks", [])
    pad_counts = count_component_pins(pads)

    src_pad = _find_pad(pads, from_pad_str)
    if not src_pad:
        return None, None, f"Pad {from_pad_str} not found"

    dst_pad = _find_pad(pads, to_pad_str)
    if not dst_pad:
        return None, None, f"Pad {to_pad_str} not found"

    # Consistent ordering
    src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)
    source = (src_pad["x"], src_pad["y"])
    dest = (dst_pad["x"], dst_pad["y"])
    trace_len = math.hypot(dest[0] - source[0], dest[1] - source[1])
    n_obs = count_visible_obstacles(pads, net, source, dest)

    target_layers = _get_target_layers()

    candidates = retrieve_and_transform(
        _collection, pads, tracks, net, source, dest,
        n=n
    )

    query_info = {
        "net": net,
        "from_pad": f"{src_pad['ref']}.{src_pad['pin']}",
        "to_pad": f"{dst_pad['ref']}.{dst_pad['pin']}",
        "from_xy": [source[0], source[1]],
        "to_xy": [dest[0], dest[1]],
        "trace_len_mm": round(trace_len, 2),
        "obstacles": n_obs,
    }

    return candidates, query_info, None


def _render_candidates_image(from_pad_str, to_pad_str, net, n=5, width=800):
    """Render candidate traces as PNG image."""
    from PIL import Image, ImageDraw

    data = _fetch_board_state()
    pads = data.get("pads", [])
    tracks = data.get("tracks", [])
    board = data.get("board", {})
    board_w = board.get("width", 60)
    board_h = board.get("height", 40)
    pad_counts = count_component_pins(pads)

    src_pad = _find_pad(pads, from_pad_str)
    dst_pad = _find_pad(pads, to_pad_str)
    if not src_pad or not dst_pad:
        return None

    src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)
    source = (src_pad["x"], src_pad["y"])
    dest = (dst_pad["x"], dst_pad["y"])

    candidates = retrieve_and_transform(
        _collection, pads, tracks, net, source, dest,
        n=n
    )

    from render_candidates import render_board_with_candidates
    img = render_board_with_candidates(
        pads, candidates, net, (board_w, board_h),
        source, dest, width=width
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _get_routable_nets():
    """Get all nets with 2+ pads that have obstacle context."""
    data = _fetch_board_state()
    pads = data.get("pads", [])
    pad_counts = count_component_pins(pads)

    nets = {}
    for p in pads:
        n = p.get("net", "")
        if n:
            nets.setdefault(n, []).append(p)

    routable = []
    for net, net_pads in sorted(nets.items()):
        if len(net_pads) < 2:
            continue

        pairs = []
        for i in range(len(net_pads)):
            for j in range(i + 1, len(net_pads)):
                src, dst = choose_source_dest(net_pads[i], net_pads[j], pad_counts)
                if not src or not dst:
                    continue
                s = (src["x"], src["y"])
                d = (dst["x"], dst["y"])
                tl = math.hypot(d[0] - s[0], d[1] - s[1])
                if tl < 2.0:
                    continue
                n_obs = count_visible_obstacles(pads, net, s, d)
                pairs.append({
                    "from": f"{src['ref']}.{src['pin']}",
                    "to": f"{dst['ref']}.{dst['pin']}",
                    "length_mm": round(tl, 2),
                    "obstacles": n_obs,
                })

        if pairs:
            routable.append({
                "net": net,
                "pads": len(net_pads),
                "pairs": pairs,
            })

    return routable

def _rollback_segments(segments, net):
    """Remove previously placed segments by deleting tracks in their bounding box."""
    for seg in segments:
        x_min = min(seg[0], seg[2]) - 0.01
        y_min = min(seg[1], seg[3]) - 0.01
        x_max = max(seg[0], seg[2]) + 0.01
        y_max = max(seg[1], seg[3]) + 0.01

        cmd = json.dumps({
            "action": "delete_tracks",
            "net": net,
            "x_min": x_min, "y_min": y_min,
            "x_max": x_max, "y_max": y_max,
        }).encode()
        req = urllib.request.Request(
            _route_server + "/", data=cmd,
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
        except Exception:
            pass

# ============================================================
# PLACEMENT
# ============================================================
def _place_trace(candidate, net, width=0.25):
    """Place a transformed trace on the board via the route server."""

    # Pre-check via clearance at all layer transition points
    segs = candidate["segments"]
    for i in range(len(segs) - 1):
        layer_a = segs[i][4] if isinstance(segs[i][4], str) else "F.Cu"
        layer_b = segs[i+1][4] if isinstance(segs[i+1][4], str) else "F.Cu"
        if layer_a != layer_b:
            # Transition point — shared endpoint
            tx, ty = segs[i][2], segs[i][3]
            cmd = json.dumps({
                "action": "check_via",
                "net": net,
                "x": tx,
                "y": ty,
            }).encode()
            req = urllib.request.Request(
                _route_server + "/", data=cmd,
                headers={"Content-Type": "application/json"})
            try:
                resp = json.loads(urllib.request.urlopen(req).read())
            except Exception as e:
                resp = {"ok": False}
            if not resp.get("ok"):
                return {
                    "ok": False,
                    "placed": 0,
                    "total": len(segs),
                    "error": f"via clearance violation at [{round(tx,2)}, {round(ty,2)}]",
                    "failed_segment": i,
                }

    # Place segments as before
    placed_segs = []
    for i, seg in enumerate(segs):
        layer = seg[4] if isinstance(seg[4], str) else "F.Cu"
        cmd = json.dumps({
            "action": "add_track",
            "net": net,
            "x1": seg[0], "y1": seg[1],
            "x2": seg[2], "y2": seg[3],
            "layer": layer,
            "width": width,
        }).encode()
        req = urllib.request.Request(
            _route_server + "/", data=cmd,
            headers={"Content-Type": "application/json"})
        try:
            resp = json.loads(urllib.request.urlopen(req).read())
        except Exception as e:
            resp = {"ok": False, "error": str(e)}

        if resp.get("ok"):
            placed_segs.append(seg)
        else:
            if placed_segs:
                _rollback_segments(placed_segs, net)
            return {
                "ok": False,
                "placed": 0,
                "total": len(segs),
                "error": resp.get("message", resp.get("error", "unknown")),
                "failed_segment": i,
            }

    return {
        "ok": True,
        "placed": len(placed_segs),
        "total": len(segs),
    }


def _get_query_image(from_pad_str, to_pad_str, net):
    """Render the DINOv2 query image for a pad pair.
    
    Returns PNG bytes or None.
    """
    from full_pattern_render import render_full_pattern

    data      = _fetch_board_state()
    pads      = data.get("pads", [])
    tracks    = data.get("tracks", [])
    pad_counts = count_component_pins(pads)

    src_pad = _find_pad(pads, from_pad_str)
    dst_pad = _find_pad(pads, to_pad_str)
    if not src_pad or not dst_pad:
        return None

    src_pad, dst_pad = choose_source_dest(src_pad, dst_pad, pad_counts)
    source = (src_pad["x"], src_pad["y"])
    dest   = (dst_pad["x"], dst_pad["y"])

    other_tracks = [t for t in tracks if t.get("net") != net]
    img = render_full_pattern(pads, other_tracks, net, source, dest)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()




def _try_place_candidates(candidates, net, width=0.25):
    """Try placing candidates in order until one passes clearance."""
    errors = []

    for i, candidate in enumerate(candidates):
        result = _place_trace(candidate, net, width)

        if result["ok"]:
            meta = candidate.get("source_meta", {})
            return {
                "ok": True,
                "candidate_index": i,
                "placed": result["placed"],
                "attempts": i + 1,
                "board": meta.get("board", ""),
                "scale": candidate["transform"]["scale"],
                "errors": errors,
            }
        else:
            errors.append({
                "candidate": i,
                "board": candidate.get("source_meta", {}).get("board", ""),
                "error": result["error"],
                "failed_segment": result["failed_segment"],
            })

    return {
        "ok": False,
        "candidate_index": -1,
        "placed": 0,
        "attempts": len(candidates),
        "errors": errors,
    }


# ============================================================
# HTTP HANDLER
# ============================================================

class PatternHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/status":
            self._json_response({
                "ok": True,
                "collection_size": _collection.count(),
                "db_path": _db_path,
                "route_server": _route_server,
            })

        elif path == "/query":
            from_pad = params.get("from", [None])[0]
            to_pad = params.get("to", [None])[0]
            net = params.get("net", [None])[0]
            n = int(params.get("n", [5])[0])

            if not from_pad or not to_pad or not net:
                self._json_response({
                    "ok": False,
                    "error": "Required: from, to, net"
                }, 400)
                return

            candidates, query_info, error = _query_candidates(
                from_pad, to_pad, net, n
            )

            if error:
                self._json_response({"ok": False, "error": error}, 400)
                return

            results = []
            for c in candidates:
                meta = c["source_meta"]
                results.append({
                    "segments": c["segments"],
                    "vias": c["vias"],
                    "match_distance": c["match_distance"],
                    "scale": c["transform"]["scale"],
                    "scale_distortion": c["scale_distortion"],
                    "rotation_deg": round(math.degrees(c["transform"]["rotation"]), 1),
                    "board": meta.get("board", ""),
                    "net": meta.get("net", ""),
                    "from_ref": meta.get("from_ref", ""),
                    "to_ref": meta.get("to_ref", ""),
                    "original_length_mm": meta.get("trace_len_mm", 0),
                    "n_vias": meta.get("n_vias", 0),
                })

            self._json_response({
                "ok": True,
                "query": query_info,
                "candidates": results,
            })

        elif path == "/render":
            from_pad = params.get("from", [None])[0]
            to_pad = params.get("to", [None])[0]
            net = params.get("net", [None])[0]
            n = int(params.get("n", [5])[0])
            width = int(params.get("width", [800])[0])

            if not from_pad or not to_pad or not net:
                self._json_response({
                    "ok": False,
                    "error": "Required: from, to, net"
                }, 400)
                return

            png = _render_candidates_image(from_pad, to_pad, net, n, width)
            if png:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(png)
            else:
                self._json_response({"ok": False, "error": "render failed"}, 500)

        elif path == "/nets":
            routable = _get_routable_nets()
            self._json_response({"ok": True, "nets": routable})

        elif path == "/query_image":
            from_pad = params.get("from", [None])[0]
            to_pad   = params.get("to",   [None])[0]
            net      = params.get("net",  [None])[0]

            if not from_pad or not to_pad or not net:
                self._json_response({"ok": False, "error": "Required: from, to, net"}, 400)
                return

            png = _get_query_image(from_pad, to_pad, net)
            if png:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(png)
            else:
                self._json_response({"ok": False, "error": "pad not found"}, 400)
                

        elif path == "/place":
            from_pad = params.get("from", [None])[0]
            to_pad = params.get("to", [None])[0]
            net = params.get("net", [None])[0]
            n = int(params.get("n", [10])[0])
            width = float(params.get("width", [0.25])[0])

            if not from_pad or not to_pad or not net:
                self._json_response({
                    "ok": False,
                    "error": "Required: from, to, net"
                }, 400)
                return

            candidates, query_info, error = _query_candidates(
                from_pad, to_pad, net, n
            )

            if error:
                self._json_response({"ok": False, "error": error}, 400)
                return

            if not candidates:
                self._json_response({
                    "ok": False,
                    "error": "no candidates found",
                    "query": query_info,
                })
                return

            result = _try_place_candidates(candidates, net, width)
            result["query"] = query_info
            self._json_response(result)

        else:
            self._json_response({
                "ok": False,
                "error": "Unknown endpoint",
                "endpoints": ["/status", "/query", "/render", "/nets", "/place"]
            }, 404)

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Pattern route server")
    parser.add_argument("--port", type=int, default=8085,
                        help="Server port (default: 8085)")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "trace_pattern_collection"),
        help="ChromaDB persist directory")
    parser.add_argument("--route-server", default="http://localhost:8084",
                        help="Route server URL")
    args = parser.parse_args()

    _init(os.path.abspath(args.db), args.route_server)

    server = http.server.HTTPServer(("0.0.0.0", args.port), PatternHandler)

    print(f"  Pattern Route Server")
    print(f"  ====================")
    print(f"  Port:         http://localhost:{args.port}")
    print(f"  Collection:   {_collection.count()} patterns")
    print(f"  Route server: {args.route_server}")
    print()
    print(f"  Endpoints:")
    print(f"    /status")
    print(f"    /query?from=REF.PIN&to=REF.PIN&net=NET&n=5")
    print(f"    /render?from=REF.PIN&to=REF.PIN&net=NET&n=5&width=800")
    print(f"    /nets")
    print(f"    /place?from=REF.PIN&to=REF.PIN&net=NET&n=200&width=0.25")
    print()
    print(f"  Ready. Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()