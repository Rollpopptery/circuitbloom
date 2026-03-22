"""
tree_to_xy.py — The Transform (tree-based)

Walks a CircuitBloom layout tree and returns centre positions in mm.

Size flows up. Position flows down. One SCALE converts cells to mm.

    position_mm = position_cells × SCALE
"""


# ============================================================
# CONSTANTS
# ============================================================

SCALE = 1.0   # mm per grid cell
GAP = 0.05    # mm between siblings


# ============================================================
# SIZE (bottom-up)
# ============================================================

def compute_size(node):
    """Set _w, _h on every node (in mm)."""
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


# ============================================================
# POSITIONS (top-down)
# ============================================================

def compute_positions(node, x=0, y=0):
    """Set _x, _y on every node (in mm, top-left corner)."""
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


# ============================================================
# CENTRES
# ============================================================

def collect_centres(node):
    """Return dict of leaf_id -> (centre_x_mm, centre_y_mm)."""
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


# ============================================================
# PUBLIC API
# ============================================================

def transform(tree, scale=None):
    """
    Layout tree -> dict of component_id -> (x_mm, y_mm) centres.

    Modifies the tree in-place (adds _w, _h, _x, _y).
    """
    if scale is not None:
        global SCALE
        SCALE = scale

    compute_size(tree)
    compute_positions(tree)
    return collect_centres(tree)


# ============================================================
# STANDALONE
# ============================================================

if __name__ == "__main__":
    import sys
    import json

    raw = sys.stdin.read()
    state = json.loads(raw)
    tree = state.get("tree") or state

    positions = transform(tree)

    print(f"SCALE: {SCALE} mm/cell")
    print(f"GAP:   {GAP} mm")
    print()
    print(f"{'Ref':6s}  {'X mm':>8s}  {'Y mm':>8s}")
    print("-" * 26)
    for name, (x, y) in sorted(positions.items()):
        print(f"{name:6s}  {x:8.2f}  {y:8.2f}")
