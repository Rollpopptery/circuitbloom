"""
grid_to_xy.py — The Transform

Converts a CSS Grid layout into x,y centre points.

Everything is calculated in cell units.
One global SCALE converts to mm at the very end.

    position_mm = position_cells × SCALE

That's the only place physical units exist.
"""

import re


# ============================================================
# THE ONE NUMBER
# ============================================================

SCALE = 1.0  # 1 cell = 1 mm

# Gaps as fractions of a cell
CELL_GAP = 0.0       # gap between cells within a group
GROUP_GAP = 0.5       # gap between groups on the board


# ============================================================
# PARSING
# ============================================================

def parse_areas(areas_str):
    """
    '"j2 j2 j2" "power usb-data mcu"'
    → [["j2","j2","j2"], ["power","usb-data","mcu"]]
    """
    rows = re.findall(r'"([^"]+)"', areas_str)
    return [row.split() for row in rows]


def parse_group(html):
    """
    Parse group HTML with either class-based or inline style spans.
    Handles both class-based and inline column counts.
    Skips empty spacer divs (no text content).
    
    → (cols, [("J1", 2, 3), ...])
    """
    # Column count: try data-cols first, then inline style, then class
    cols_data = re.search(r'data-cols="(\d+)"', html)
    cols_inline = re.search(r'repeat\((\d+)', html)
    cols_class = re.search(r'cols(\d+)', html)
    if cols_data:
        cols = int(cols_data.group(1))
    elif cols_inline:
        cols = int(cols_inline.group(1))
    elif cols_class:
        cols = int(cols_class.group(1))
    else:
        cols = 4

    components = []

    # Try inline style spans — only include divs with text content
    for m in re.finditer(r'grid-column:\s*span\s+(\d+);\s*grid-row:\s*span\s+(\d+)[^>]*>([^<]*)<', html):
        col_span = int(m.group(1))
        row_span = int(m.group(2))
        name = m.group(3).strip()
        if name:  # skip empty spacer divs
            components.append((name, col_span, row_span))

    # Fall back to class-based spans if no inline found
    if not components:
        for m in re.finditer(r'class="s(\d+)x(\d+)"[^>]*>([^<]+)<', html):
            col_span = int(m.group(1))
            row_span = int(m.group(2))
            name = m.group(3).strip()
            components.append((name, col_span, row_span))

    return cols, components


# ============================================================
# DENSE PACKING (in cell units)
# ============================================================

def dense_pack(cols, components):
    """
    Simulate CSS grid-auto-flow: dense.

    Returns:
        positions: dict of name → (cx, cy) centre in cell units
                   (includes gaps between cells)
        width:     total group width in cell units
        height:    total group height in cell units
    """
    max_rows = 100
    occupied = [[False] * cols for _ in range(max_rows)]
    positions = {}

    for name, col_span, row_span in components:
        placed = False
        for r in range(max_rows - row_span + 1):
            for c in range(cols - col_span + 1):
                fits = all(
                    not occupied[r + dr][c + dc]
                    for dr in range(row_span)
                    for dc in range(col_span)
                )
                if fits:
                    for dr in range(row_span):
                        for dc in range(col_span):
                            occupied[r + dr][c + dc] = True

                    # Centre point including gaps
                    # Cell n starts at: n * (1 + CELL_GAP)
                    # Centre of span s starting at cell n:
                    #   n * (1 + CELL_GAP) + s * (1 + CELL_GAP) / 2 - CELL_GAP / 2
                    step = 1 + CELL_GAP
                    cx = c * step + col_span * step / 2.0 - CELL_GAP / 2.0
                    cy = r * step + row_span * step / 2.0 - CELL_GAP / 2.0

                    positions[name] = (cx, cy)
                    placed = True
                    break
            if placed:
                break

        if not placed:
            raise ValueError(f"Cannot place {name} ({col_span}x{row_span}) in {cols}-col grid")

    # Total size including gaps
    total_rows = 0
    for r in range(max_rows):
        if any(occupied[r]):
            total_rows = r + 1

    step = 1 + CELL_GAP
    width = cols * step - CELL_GAP
    height = total_rows * step - CELL_GAP

    return positions, width, height


# ============================================================
# BOARD LAYOUT (in cell units)
# ============================================================

def find_group_cell(grid, name):
    """Top-left (row, col) of named area."""
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell == name:
                return r, c
    return None


def find_group_span(grid, name):
    """(col_span, row_span) of named area."""
    rows = set()
    cols = set()
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell == name:
                rows.add(r)
                cols.add(c)
    if not rows:
        return 1, 1
    return len(cols), len(rows)


def board_layout(grid, group_sizes):
    """
    Calculate x,y offset of each board cell in cell units.

    Each board column width = widest group in that column.
    Each board row height = tallest group in that row.

    Returns (col_offsets, row_offsets) in cell units.
    """
    num_cols = len(grid[0]) if grid else 0
    num_rows = len(grid)

    col_widths = [0.0] * num_cols
    row_heights = [0.0] * num_rows

    measured = set()

    for r, row in enumerate(grid):
        for c, name in enumerate(row):
            if name == '.' or name in measured:
                continue
            measured.add(name)
            if name not in group_sizes:
                continue

            gw, gh = group_sizes[name]
            g_cs, g_rs = find_group_span(grid, name)

            per_col = gw / g_cs
            for dc in range(g_cs):
                col_widths[c + dc] = max(col_widths[c + dc], per_col)

            per_row = gh / g_rs
            for dr in range(g_rs):
                row_heights[r + dr] = max(row_heights[r + dr], per_row)

    col_offsets = [0.0]
    for w in col_widths:
        col_offsets.append(col_offsets[-1] + w + GROUP_GAP)

    row_offsets = [0.0]
    for h in row_heights:
        row_offsets.append(row_offsets[-1] + h + GROUP_GAP)

    return col_offsets, row_offsets


# ============================================================
# THE TRANSFORM
# ============================================================

def transform(state, scale=None):
    """
    Grid state → component positions in mm.

    Input:
        state["areas"]  — grid-template-areas string
        state["groups"] — {group_name: html_fragment}
        scale           — mm per cell unit (default: SCALE)

    Output:
        dict of component_name → (x_mm, y_mm)
        from board top-left corner (0, 0)
    """
    if scale is None:
        scale = SCALE

    grid = parse_areas(state["areas"])

    # Step 1: pack each group, get local positions and sizes (cell units)
    group_local = {}
    group_sizes = {}

    for group_name, html in state["groups"].items():
        cols, components = parse_group(html)
        positions, width, height = dense_pack(cols, components)
        group_local[group_name] = positions
        group_sizes[group_name] = (width, height)

    # Step 2: board-level offsets (cell units)
    col_offsets, row_offsets = board_layout(grid, group_sizes)

    # Step 3: absolute positions (cell units → mm)
    result = {}

    for group_name, local_positions in group_local.items():
        cell = find_group_cell(grid, group_name)
        if cell is None:
            continue
        board_row, board_col = cell

        group_x = col_offsets[board_col]
        group_y = row_offsets[board_row]

        for comp_name, (local_x, local_y) in local_positions.items():
            # Cell units → mm (the one multiplication)
            x_mm = (group_x + local_x) * scale
            y_mm = (group_y + local_y) * scale

            result[comp_name] = (x_mm, y_mm)

    return result


def board_size(state, scale=None):
    """
    Calculate total board dimensions in mm.
    """
    positions = transform(state, scale)
    if not positions:
        return 0.0, 0.0
    max_x = max(x for x, y in positions.values())
    max_y = max(y for x, y in positions.values())
    return max_x, max_y


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == '__main__':
    import sys
    import json

    raw = sys.stdin.read()
    state = json.loads(raw)

    if not state.get("areas") or not state.get("groups"):
        print("Error: no layout loaded", file=sys.stderr)
        sys.exit(1)

    positions = transform(state)

    print(f"SCALE: {SCALE} mm/cell")
    print(f"CELL_GAP: {CELL_GAP} cells")
    print(f"GROUP_GAP: {GROUP_GAP} cells")
    print()
    print(f"{'Ref':6s}  {'X mm':>8s}  {'Y mm':>8s}")
    print("-" * 26)
    for name, (x, y) in sorted(positions.items()):
        print(f"{name:6s}  {x:8.2f}  {y:8.2f}")

    w, h = board_size(state)
    print(f"\nBoard extent: {w:.1f} x {h:.1f} mm")