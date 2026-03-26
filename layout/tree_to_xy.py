"""
tree_to_xy.py — Placement resolver

Supports two formats:

1. Grid placement (flat dict):
   {"XJ1": {"col": 2, "row": 1, "w": 7, "h": 3}, ...}
   position_mm = col * SCALE, centre = position + size/2

2. Legacy layout tree (nested row/column):
   {"id": "board", "arrange": "column", "children": [...]}

SCALE converts grid cells to mm.
"""

SCALE = 1.0  # mm per grid cell


# ============================================================
# GRID PLACEMENT (new)
# ============================================================

def resolve_placement(placement):
    """placement dict -> {ref: (centre_x_mm, centre_y_mm)}."""
    centres = {}
    for ref, p in placement.items():
        x = p["col"] * SCALE + p["w"] * SCALE / 2
        y = p["row"] * SCALE + p["h"] * SCALE / 2
        centres[ref] = (x, y)
    return centres


def placement_rects(placement):
    """placement dict -> list of {id, x, y, w, h, type} for viewer."""
    rects = []
    for ref, p in placement.items():
        rects.append({
            "id": ref,
            "x": p["col"] * SCALE,
            "y": p["row"] * SCALE,
            "w": p["w"] * SCALE,
            "h": p["h"] * SCALE,
            "type": "component"
        })
    return rects


# ============================================================
# LEGACY TREE (kept for backward compat)
# ============================================================

GAP = 0.05


def compute_size(node):
    if "w" in node:
        node["_w"] = node["w"] * SCALE
        node["_h"] = node["h"] * SCALE
        return
    children = node.get("children", [])
    if not children:
        node["_w"] = 0
        node["_h"] = 0
        return
    for c in children:
        compute_size(c)
    is_row = node["arrange"] == "row"
    main = 0
    cross = 0
    for i, c in enumerate(children):
        main += c["_w"] if is_row else c["_h"]
        if i > 0:
            main += GAP
        cross = max(cross, c["_h"] if is_row else c["_w"])
    node["_w"] = main if is_row else cross
    node["_h"] = cross if is_row else main


def compute_positions(node, x=0, y=0):
    node["_x"] = x
    node["_y"] = y
    children = node.get("children")
    if not children:
        return
    is_row = node["arrange"] == "row"
    cursor = 0
    for i, c in enumerate(children):
        if i > 0:
            cursor += GAP
        cx = x + cursor if is_row else x
        cy = y if is_row else y + cursor
        compute_positions(c, cx, cy)
        cursor += c["_w"] if is_row else c["_h"]


def collect_centres(node):
    result = {}
    _walk(node, result)
    return result


def _walk(node, result):
    if "w" in node:
        cx = node["_x"] + node["_w"] / 2
        cy = node["_y"] + node["_h"] / 2
        if not node["id"].startswith("_"):
            result[node["id"]] = (cx, cy)
        return
    for c in node.get("children", []):
        _walk(c, result)


def collect_rects(node):
    rects = []
    _walk_rects(node, rects)
    return rects


def _walk_rects(node, rects):
    nid = node.get("id", "")
    x = node.get("_x", 0)
    y = node.get("_y", 0)
    w = node.get("_w", 0)
    h = node.get("_h", 0)
    if "children" in node:
        rects.append({"id": nid, "x": x, "y": y, "w": w, "h": h, "type": "group"})
        for c in node["children"]:
            _walk_rects(c, rects)
    elif nid.startswith("_"):
        rects.append({"id": nid, "x": x, "y": y, "w": w, "h": h, "type": "spacer"})
    else:
        rects.append({"id": nid, "x": x, "y": y, "w": w, "h": h, "type": "component"})


# ============================================================
# PUBLIC API
# ============================================================

def transform(tree_or_placement, scale=None):
    """Resolve positions from either placement dict or legacy tree.

    Returns {ref: (centre_x_mm, centre_y_mm)}.
    """
    if scale is not None:
        global SCALE
        SCALE = scale

    # Detect format: placement dict has string keys mapping to dicts with "col"
    if isinstance(tree_or_placement, dict) and not tree_or_placement.get("id"):
        return resolve_placement(tree_or_placement)

    # Legacy tree
    compute_size(tree_or_placement)
    compute_positions(tree_or_placement)
    return collect_centres(tree_or_placement)


def get_rects(tree_or_placement):
    """Get rectangles from either format."""
    if isinstance(tree_or_placement, dict) and not tree_or_placement.get("id"):
        return placement_rects(tree_or_placement)
    return collect_rects(tree_or_placement)


# ============================================================
# STANDALONE
# ============================================================

if __name__ == "__main__":
    import sys
    import json

    raw = sys.stdin.read()
    data = json.loads(raw)

    positions = transform(data)

    print(f"SCALE: {SCALE} mm/cell")
    print()
    print(f"{'Ref':6s}  {'X mm':>8s}  {'Y mm':>8s}")
    print("-" * 26)
    for name, (x, y) in sorted(positions.items()):
        print(f"{name:6s}  {x:8.2f}  {y:8.2f}")
