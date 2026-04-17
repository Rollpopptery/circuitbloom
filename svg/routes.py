#!/usr/bin/env python3
"""
routes.py — URL dispatch table for svg_server.py.

To add a new endpoint, add one entry to ROUTES or POST_ROUTES.
svg_server.py never needs to be edited for new endpoints.

Each handler signature:
    GET:  handler(board, corridors, params) -> (code, ct, body)
    POST: handler(board, body)             -> (code, ct, body)
         or handler(board)                -> (code, ct, body)

Prefix routes (startswith match) are listed in PREFIX_ROUTES.

API manifest:
    GET /api returns a JSON list of all available endpoints with their
    parameters and defaults. The viewer uses this to build the API explorer
    panel dynamically — adding a new endpoint here auto-populates the UI.
"""

import json
import urllib.parse


# ── API manifest ──────────────────────────────────────────────────────────────

API_MANIFEST = [
    # ── Board ──────────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Board",
        "path": "/board.svg",
        "desc": "Capture board from KiCad",
        "params": [],
        "returns": "svg",
    },
    {
        "method": "GET", "group": "Board",
        "path": "/svg",
        "desc": "Get current board SVG",
        "params": [
            {"name": "compact", "default": "false", "desc": "Strip labels for LLM"},
        ],
        "returns": "svg",
    },
    {
        "method": "GET", "group": "Board",
        "path": "/svg/info",
        "desc": "Board summary (pads, tracks, vias, layers)",
        "params": [],
        "returns": "json",
    },
    {
        "method": "GET", "group": "Board",
        "path": "/push",
        "desc": "Push routed tracks and vias to KiCad",
        "params": [],
        "returns": "json",
    },
    {
        "method": "GET", "group": "Board",
        "path": "/export/dsn",
        "desc": "Export board as Specctra DSN",
        "params": [],
        "returns": "json",
    },
    # ── Routing ────────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Routing",
        "path": "/route",
        "desc": "Run FreeRouting autorouter",
        "params": [
            {"name": "clearance",   "default": "0.2",  "desc": "Clearance between traces (mm)"},
            {"name": "track_width", "default": "0.25", "desc": "Default track width (mm)"},
            {"name": "via_od",      "default": "0.6",  "desc": "Via outer diameter (mm)"},
            {"name": "via_drill",   "default": "0.3",  "desc": "Via drill diameter (mm)"},
            {"name": "timeout",     "default": "300",  "desc": "Max routing time (seconds)"},
        ],
        "returns": "json",
    },
    {
        "method": "GET", "group": "Routing",
        "path": "/svg/check",
        "desc": "Check overlay polylines for intersections",
        "params": [],
        "returns": "json",
    },
    # ── Keepouts ───────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Keepouts",
        "path": "/keepouts/draw",
        "desc": "Draw inter-pin keepout barriers",
        "params": [
            {"name": "max_dist", "default": "2.0",  "desc": "Max pad-to-pad distance (mm)"},
            {"name": "width",    "default": "0.2",  "desc": "Thickness along pad axis (mm)"},
            {"name": "height",   "default": "0.6",  "desc": "Base perpendicular extent (mm)"},
            {"name": "length",   "default": "0.0",  "desc": "Extra funnelling length (mm)"},
            {"name": "ref",      "default": "",      "desc": "Component ref (blank = all)"},
        ],
        "returns": "svg",
    },
    # ── Freespace ──────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Freespace",
        "path": "/freespace/draw",
        "desc": "Draw free-space polygon overlay",
        "params": [
            {"name": "clearance", "default": "1.27", "desc": "Pad clearance (mm)"},
        ],
        "returns": "svg",
    },
    {
        "method": "GET", "group": "Freespace",
        "path": "/freespace/lines",
        "desc": "Draw free-space corners and sweep lines",
        "params": [
            {"name": "clearance", "default": "1.27", "desc": "Pad clearance (mm)"},
        ],
        "returns": "svg",
    },
    # ── Query ──────────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Query",
        "path": "/svg/ref/",
        "desc": "Query all pads for a component ref",
        "params": [
            {"name": "ref", "default": "U1", "desc": "Component reference"},
        ],
        "returns": "json",
        "suffix_param": "ref",
    },
    {
        "method": "GET", "group": "Query",
        "path": "/svg/net/",
        "desc": "Query all elements on a net",
        "params": [
            {"name": "net", "default": "GND", "desc": "Net name"},
        ],
        "returns": "json",
        "suffix_param": "net",
    },
    {
        "method": "GET", "group": "Query",
        "path": "/svg/element/",
        "desc": "Get a single element by id",
        "params": [
            {"name": "id", "default": "pad-U1-1", "desc": "Element id"},
        ],
        "returns": "json",
        "suffix_param": "id",
    },
    # ── Overlays ───────────────────────────────────────────────────────────
    {
        "method": "POST", "group": "Overlays",
        "path": "/svg/clear_overlays",
        "desc": "Clear all overlay elements",
        "params": [],
        "returns": "svg",
    },
    {
        "method": "POST", "group": "Overlays",
        "path": "/svg/add",
        "desc": "Add a new SVG element to overlays",
        "params": [
            {"name": "tag",       "default": "circle",    "desc": "SVG tag"},
            {"name": "attrs",     "default": '{"cx":10,"cy":10,"r":0.5,"fill":"#ff0000","id":"marker-1"}', "desc": "Attrs JSON"},
            {"name": "parent_id", "default": "overlays",  "desc": "Parent element id"},
        ],
        "returns": "json",
        "body_template": '{"tag":"{tag}","attrs":{attrs},"parent_id":"{parent_id}"}',
    },
    {
        "method": "POST", "group": "Overlays",
        "path": "/svg/update",
        "desc": "Update attrs on an existing element",
        "params": [
            {"name": "id",    "default": "marker-1",          "desc": "Element id"},
            {"name": "attrs", "default": '{"fill":"#00ff00"}', "desc": "Attrs to merge (JSON)"},
        ],
        "returns": "json",
        "body_template": '{"id":"{id}","attrs":{attrs}}',
    },
    {
        "method": "POST", "group": "Overlays",
        "path": "/svg/remove",
        "desc": "Remove an element by id",
        "params": [
            {"name": "id", "default": "marker-1", "desc": "Element id"},
        ],
        "returns": "json",
        "body_template": '{"id":"{id}"}',
    },
    # ── Corridors ──────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Corridors",
        "path": "/corridors",
        "desc": "List routing corridors",
        "params": [],
        "returns": "json",
    },
    {
        "method": "GET", "group": "Corridors",
        "path": "/corridors/draw",
        "desc": "Draw corridors into overlays",
        "params": [
            {"name": "clearance", "default": "1.27", "desc": "Clearance (mm)"},
        ],
        "returns": "svg",
    },
    # ── Courtyards ─────────────────────────────────────────────────────────
    {
        "method": "GET", "group": "Courtyards",
        "path": "/courtyards/draw",
        "desc": "Draw component courtyards into overlays",
        "params": [
            {"name": "margin", "default": "0.5", "desc": "Margin (mm)"},
        ],
        "returns": "svg",
    },
]


def _api_manifest_handler(b, c, p):
    return 200, 'application/json', json.dumps(API_MANIFEST).encode()


# ── Parameter helpers ─────────────────────────────────────────────────────────

def _clearance(params, default=1.27):
    return float(params.get('clearance', [default])[0])

def _margin(params, default=0.5):
    return float(params.get('margin', [default])[0])

def _float(params, key, default):
    return float(params.get(key, [default])[0])

def _int(params, key, default):
    return int(params.get(key, [default])[0])

def _str(params, key, default=None):
    return params.get(key, [default])[0]


# ── GET route factories ───────────────────────────────────────────────────────

def _svg_routes():
    from svg_endpoints import (
        handle_get_svg, handle_get_element,
        handle_query_net, handle_query_ref,
        handle_info, handle_check_conflicts,
    )
    return {
        '/svg':       lambda b, c, p: handle_get_svg(b, compact=p.get('compact', ['false'])[0].lower() in ('true','1','yes')),
        '/svg/info':  lambda b, c, p: handle_info(b),
        '/svg/check': lambda b, c, p: handle_check_conflicts(b),
    }

def _freespace_routes():
    from freespace_endpoints import (
        handle_freespace_draw, handle_freespace_lines, handle_keepouts_draw,
    )
    return {
        '/freespace/draw':  lambda b, c, p: handle_freespace_draw(b, _clearance(p)),
        '/freespace/lines': lambda b, c, p: handle_freespace_lines(b, _clearance(p)),
        '/keepouts/draw':   lambda b, c, p: handle_keepouts_draw(
            b,
            max_dist = _float(p, 'max_dist', 2.0),
            width    = _float(p, 'width',    0.2),
            height   = _float(p, 'height',   0.6),
            length   = _float(p, 'length',   0.0),
            ref      = _str(p,  'ref'),
        ),
    }

def _corridor_routes():
    from corridor_endpoints import (
        handle_corridors_draw, handle_corridors_list,
        handle_corridors_describe, handle_corridors_path,
    )
    return {
        '/corridors':          lambda b, c, p: handle_corridors_list(c),
        '/corridors/draw':     lambda b, c, p: handle_corridors_draw(b, c, _clearance(p)),
        '/corridors/describe': lambda b, c, p: handle_corridors_describe(c),
    }

def _courtyard_routes():
    from courtyard_endpoints import handle_courtyards_draw, handle_courtyards_json
    return {
        '/courtyards':      lambda b, c, p: handle_courtyards_json(b, _margin(p)),
        '/courtyards/draw': lambda b, c, p: handle_courtyards_draw(b, _margin(p)),
    }

def _dsn_routes():
    from svg_to_dsn import board_to_dsn
    def _export_dsn(b, c, p):
        if b is None:
            return 404, 'application/json', b'{"ok":false,"error":"no board loaded"}'
        return 200, 'text/plain', board_to_dsn(b).encode()
    return {
        '/export/dsn': _export_dsn,
    }


# ── Prefix GET routes ─────────────────────────────────────────────────────────

def _prefix_routes():
    from svg_endpoints import handle_get_element, handle_query_net, handle_query_ref
    from corridor_endpoints import handle_corridors_path
    return {
        '/svg/element/':   lambda b, c, p, s: handle_get_element(b, s),
        '/svg/net/':       lambda b, c, p, s: handle_query_net(b, s),
        '/svg/ref/':       lambda b, c, p, s: handle_query_ref(b, s),
        '/corridors/path': lambda b, c, p, s: handle_corridors_path(
            b, c, p.get('from',[''])[0], p.get('to',[''])[0]),
    }


# ── POST routes ───────────────────────────────────────────────────────────────

def _post_routes():
    from svg_endpoints import (
        handle_add, handle_update, handle_remove, handle_clear_overlays,
    )
    return {
        '/svg/add':            (handle_add,            True),
        '/svg/update':         (handle_update,         True),
        '/svg/remove':         (handle_remove,         True),
        '/svg/clear_overlays': (handle_clear_overlays, False),
    }


# ── Registry ──────────────────────────────────────────────────────────────────

def build_get_routes() -> dict:
    routes = {}
    for factory in [
        _svg_routes,
        _freespace_routes,
        _corridor_routes,
        _courtyard_routes,
        _dsn_routes,
    ]:
        routes.update(factory())
    routes['/api'] = _api_manifest_handler
    return routes

def build_prefix_routes() -> dict:
    return _prefix_routes()

def build_post_routes() -> dict:
    return _post_routes()