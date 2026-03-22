# CircuitBloom — Tree-based component layout

## What it is

A PCB component placement system where layout is described as a single JSON tree. Every node is either a **leaf** (a component with a size) or a **group** (a container that arranges its children in a row or column). That's it.

Size flows up. Position flows down. Arrangements within arrangements.

## The tree format

Every node has an `id`. Beyond that, there are exactly two kinds:

### Leaf (a component)

```json
{ "id": "U2", "w": 10, "h": 8 }
```

- `w` and `h` are in grid cells (each cell = 28px in the viewer, maps to real footprint units in KiCad)
- A leaf has no children. It is a rectangle.

### Group (a container)

```json
{
  "id": "power",
  "arrange": "row",
  "children": [
    { "id": "J1", "w": 6, "h": 9 },
    { "id": "U1", "w": 4, "h": 5 }
  ]
}
```

- `arrange` is `"row"` or `"column"`. Nothing else.
- `children` is an ordered array of nodes (leaves or more groups).
- A group has no `w` or `h` — its size is derived from its children.

### That's the entire spec

There are no other properties. No alignment, no padding, no z-index, no grid-column-span. If you need vertical offset, add a spacer leaf (e.g. `{"id": "_spacer", "w": 1, "h": 4}`).

## How rendering works

Three functions, run in order:

### 1. computeSize (bottom-up)

Walk the tree from leaves to root.

- **Leaf**: `_w = w * CELL`, `_h = h * CELL`
- **Group with `arrange: "row"`**: `_w = sum of children _w + gaps`, `_h = max of children _h`
- **Group with `arrange: "column"`**: `_w = max of children _w`, `_h = sum of children _h + gaps`

After this pass, every node knows its pixel dimensions.

### 2. computePositions (top-down)

Walk the tree from root to leaves.

- Root starts at `(0, 0)`.
- For each group, step through children with a cursor along the arrange axis:
  - **Row**: each child is placed at `(x + cursor, y)`, cursor advances by `child._w + GAP`
  - **Column**: each child is placed at `(x, y + cursor)`, cursor advances by `child._h + GAP`

After this pass, every node knows its `(_x, _y)` position.

### 3. render (draw)

Walk the tree and create absolutely-positioned `<div>` elements:

- **Leaf**: solid border, centered label, positioned at `(_x, _y)` with size `(_w, _h)`
- **Group**: dashed border outline with a label, contains its children visually

All positions are absolute within a single `#board` container. The board is auto-scaled to fit the viewport.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `CELL` | 28 | Pixels per grid unit |
| `GAP` | 1 | Pixels between siblings |

## The server

Two HTTP servers, no dependencies beyond Python stdlib.

### Port 8080 — Browser

Serves the viewer page. The tree and component metadata are injected as JS variables. The page polls `/version` every 400ms and reloads on change.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Full HTML page with embedded tree |
| `/version` | GET | Returns `{"v": N}` |
| `/export` | POST | Triggers `export_kicad.py` |

### Port 8081 — Agent

Accepts layout updates and tree operations as JSON.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | POST | Update state or run tree operations |
| `/` | GET | Full state dump |
| `/tree` | GET | Layout tree only |
| `/components` | GET | Component metadata only |
| `/component/{id}` | GET | Single component metadata |

#### POST body

All fields are optional. Omit what you don't need. Every POST increments the version, which triggers a browser reload.

```json
{
  "tree": { ... },
  "components": { "U2": {"shape": [10,8], "rotation": 0, "offset": [0, -9], "type": "SOIC-14"} },
  "swap": ["R1", "R6"],
  "rotate_leaf": "J2",
  "rotate": { "J2": 90 },
  "set_rotation": { "J2": 90 }
}
```

| Field | Type | What it does |
|-------|------|-------------|
| `tree` | object | Replace the entire layout tree |
| `components` | object | Update component metadata (merge) |
| `swap` | `[id_a, id_b]` | Swap two nodes in the tree by id |
| `rotate_leaf` | `id` | Swap a leaf's w and h in the tree (dimensions only) |
| `rotate` | `{id: degrees}` | Set rotation and auto-swap dimensions for 90°/270° changes |
| `set_rotation` | `{id: degrees}` | Set rotation angle only (no dimension swap) |

#### Tree operations vs full tree replacement

Use **tree operations** (`swap`, `rotate_leaf`) for targeted edits. The server walks its local copy of the tree and modifies only the nodes you name. Nothing else is touched.

Use **full tree replacement** (`tree`) only when the structure itself changes — adding groups, moving nodes between groups, changing arrange directions. Even then, prefer building on the current tree (GET `/tree`, edit, POST back) over writing one from scratch.

## Component metadata

Stored separately from the tree. The tree only cares about `id`, `w`, `h`. The metadata carries what KiCad needs:

```json
{
  "shape": [10, 8],
  "rotation": 0,
  "offset": [0, -9],
  "type": "SOIC-14"
}
```

- `shape`: original `[w, h]` of the footprint
- `rotation`: degrees (0, 90, 180, 270)
- `offset`: `[x, y]` in mm, in the component's local frame (rotates with the component). For parts whose KiCad origin is not at the footprint centre (e.g. connectors with origin at pin 1). Defaults to `[0, 0]` if omitted.
- `type`: footprint name for KiCad

## How to use it

### Start the server

```bash
python circuitbloom.py
```

### Send a layout

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d @layout.json
```

Or inline:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d '{
  "tree": {
    "id": "board", "arrange": "column", "children": [
      {"id": "J2", "w": 21, "h": 4},
      {
        "id": "middle", "arrange": "row", "children": [
          {"id": "U2", "w": 10, "h": 8},
          {"id": "C2", "w": 5, "h": 2}
        ]
      },
      {"id": "J3", "w": 21, "h": 4}
    ]
  }
}'
```

### Read current state

```bash
curl http://localhost:8081          # everything
curl http://localhost:8081/tree     # just the tree
```

### Common operations

**Swap two components** — server walks the tree, touches only those two nodes:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d '{"swap": ["R1", "R6"]}'
```

**Rotate a leaf** — swaps its w and h in the tree:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d '{"rotate_leaf": "D2"}'
```

**Send a full tree** — from a file or inline:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -d @layout.json
```

**Read current state:**

```bash
curl http://localhost:8081          # everything
curl http://localhost:8081/tree     # just the tree
```

### Structural changes (require full tree POST)

These change the shape of the tree itself, so they need a full tree replacement. GET the current tree, edit it, POST it back.

**Move a component to a different group**: remove it from one children array, add it to another.

**Change row to column**: change `"arrange": "row"` to `"arrange": "column"` on a group.

**Add vertical spacing**: insert a spacer leaf `{"id": "_spacer", "w": 1, "h": N}`. Spacers are invisible leaves that push siblings down or across without any special alignment system.

**Reorder groups**: reorder entries in the parent's children array. Left-to-right in a row, top-to-bottom in a column.

**Nest deeper**: wrap nodes in a new group with its own arrange direction.

### Design principles

The system has five primitives and nothing else:

1. **Leaves** — rectangles with a size
2. **Groups** — row or column containers
3. **Spacers** — invisible leaves for alignment
4. **Swap** — safe exchange of two nodes by id
5. **Rotate** — flip a leaf's dimensions

No CSS grid. No packing algorithm. No column counts. No alignment properties. Groups give nesting, row/column gives direction, spacers give offset, swap gives safe edits. That covers every layout move.