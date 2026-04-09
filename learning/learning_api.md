# Trace Pattern Learning System

## Overview

A pattern-matching PCB autorouter. Human-drawn traces are extracted from
routed KiCad boards, their surrounding pad patterns rendered as images,
encoded with DINOv2 visual embeddings, and stored in ChromaDB. When routing
a new board, the system finds visually similar pad arrangements and returns
proven trace shapes — transformed, validated, and placed automatically.

## Pipeline

```
 ┌─────────────┐     ┌──────────────┐     ┌────────────┐
 │  .kicad_pcb │────►│  Route       │────►│  ChromaDB  │
 │  files      │     │  Server      │     │  27K+      │
 │  (routed)   │     │  (8084)      │     │  patterns  │
 └─────────────┘     └──────┬───────┘     └─────┬──────┘
                            │                    │
                     capture board          DINOv2 384-dim
                     extract routes         embeddings
                     render pad patterns    + trace payloads
                            │                    │
                            ▼                    ▼
 ┌─────────────┐     ┌──────────────┐     ┌────────────┐
 │  Unrouted   │────►│  Pattern     │────►│  Placed    │
 │  board      │     │  Server      │     │  traces    │
 │  (target)   │     │  (8085)      │     │            │
 └─────────────┘     └──────────────┘     └────────────┘
                     query → match →
                     transform → validate →
                     place with rollback
```

## Collection Pipeline

### Harvest → Flatten → Deduplicate → Index

1. **Harvest**: `harvest_kicad.sh` clones GitHub repos containing `.kicad_pcb` files
2. **Flatten**: `collect_kicad.sh` copies all `.kicad_pcb` into a single directory
3. **Deduplicate**: `md5sum` removes identical board files
4. **Index**: `collect_trace_patterns.py` opens each board via KiCad, extracts
   routes, renders pad patterns, encodes with DINOv2, stores in ChromaDB

```bash
bash harvest_kicad.sh
bash collect_kicad.sh <source_dir> <dest_dir>
cd <dest_dir> && md5sum *.kicad_pcb | sort | awk 'seen[$1]++ {print $2}' | xargs rm -v
python collect_trace_patterns.py <dest_dir>         # fresh build
python collect_trace_patterns.py <dest_dir> --append # add to existing
```

### Collection Filters

Each route is evaluated before indexing:

- **Minimum trace length**: 2mm
- **Minimum obstacle pads in window**: 3
- **Minimum segment count**: 2

Routes that fail any filter are skipped. Approximately 60% are rejected.

### What Gets Stored

For each qualifying route:

- **Embedding**: DINOv2 CLS token (384 floats, normalised)
- **Segments**: `[[x1, y1, x2, y2, layer_index], ...]`
- **Vias**: `[[x, y], ...]`
- **Metadata**: board name, net, from/to pad references and coordinates,
  trace length, layer count, segment count, via count

Layer indices follow physical stackup order (0 = top, N = bottom),
derived from KiCad's `copper_layers` list. Not arbitrary layer names.

## Routing Pipeline

### Ratsnest → Query → Transform → Validate → Place

1. **Ratsnest**: Route server computes minimum spanning tree per net (Prim's algorithm)
2. **Query**: Pattern server renders pad pattern image, encodes with DINOv2,
   searches ChromaDB for N closest matches
3. **Transform**: Each candidate trace is translated, rotated, and scaled
   to fit the target board's source and destination pads
4. **Sort**: Candidates ranked by scale distortion (|scale - 1.0|)
5. **Validate**: Each segment sent to route server's clearance checker
6. **Rollback**: If any segment fails, all placed segments from that
   candidate are removed
7. **Place**: First candidate passing all clearance checks stays on the board

### Full Board Route (one command)

```bash
curl http://localhost:8084/ratsnest | python3 -c "
import json,sys
data = json.load(sys.stdin)
for net, edges in sorted(data.items()):
    for e in edges:
        print(f'curl \"http://localhost:8085/place?from={e[\"from\"]}&to={e[\"to\"]}&net={net}&n=200\"')
" | bash
```

## Pattern Server API (port 8085)

### GET /status

Collection size and server info.

### GET /query?from=REF.PIN&to=REF.PIN&net=NET&n=200

Returns N candidate traces as JSON, sorted by scale distortion.

Each candidate contains:
- `segments` — transformed coordinates with layer indices
- `vias` — transformed via positions
- `match_distance` — DINOv2 cosine distance
- `scale` / `scale_distortion` — how much the trace was stretched
- `rotation_deg` — angular difference from stored to target
- `board` — source board name
- `original_length_mm` — trace length before transform

### GET /render?from=REF.PIN&to=REF.PIN&net=NET&n=5&width=800

Returns PNG image with all candidates overlaid on the board layout.
Each candidate in a different colour, pads colour-coded by net relationship.

### GET /place?from=REF.PIN&to=REF.PIN&net=NET&n=200&width=0.25

Auto-place a trace. Tries candidates until one passes clearance.

Returns:
- `ok` — whether a trace was placed
- `candidate_index` — which candidate succeeded
- `attempts` — how many were tried
- `scale` — scale of the placed trace
- `board` — source board of the placed trace
- `errors` — details of each failed attempt

### GET /nets

All routable pad pairs on the current board with obstacle counts.

## Route Server API (port 8084)

### GET /ratsnest

Minimum spanning tree per net. Returns the pad-to-pad pairs needed
to fully route the board.

### GET /capture_kicad

Captures current board state from KiCad via IPC API.

### POST / (action: add_track)

Places a single track segment with clearance validation.

### POST / (action: open_board)

Opens a `.kicad_pcb` file in KiCad and captures it.

## Pad Pattern Rendering

64×64 black image with coloured dots representing pad centres:

- **Red**: foreign net (obstacle)
- **Blue**: same net
- **Green**: GND
- **Black**: empty

Window: bounding box of source and destination + 25% margin, forced square.

### Source/Destination Selection

Deterministic ordering for consistent image generation:

1. Higher pin count component is source
2. Tiebreak: lower X
3. Tiebreak: lower Y

## DINOv2 Encoding

Model: `facebook/dinov2-small` (384-dim, ~86MB)

Self-supervised visual encoder. Understands spatial structure and colour
without text training. Provides rotation tolerance — similar pad
arrangements match even when rotated.

Chosen over CLIP: similarity range 0.22 vs 0.13. CLIP sees "dots on black"
for everything. DINOv2 sees spatial structure.

## Trace Transform

Stored trace → target board:

1. Translate stored source to origin
2. Rotate by `atan2(target) - atan2(stored)`
3. Scale by `target_length / stored_length`
4. Translate to target source position

Applied to all segment endpoints and via positions. Preserves trace shape.

## Layer Handling

- Stored as stackup indices (0 = top, N = bottom)
- Authoritative order from KiCad `copper_layers`
- Caller maps indices to target board's layer names at placement time
- Traces using more layers than the target board can be filtered

## Key Modules

| Module | Responsibility |
|--------|---------------|
| `pad_pattern_render.py` | Image rendering, source selection, obstacle counting |
| `trace_patterndb.py` | DINOv2 encoding, ChromaDB read/write |
| `trace_transform.py` | Geometric transform (translate, rotate, scale) |
| `collect_trace_patterns.py` | Batch indexing via route server |
| `pattern_route_server.py` | HTTP API, placement with rollback |
| `render_candidates.py` | Visual candidate overlay |
| `pattern_diag.py` | Database statistics and diagnostics |
| `harvest_kicad.sh` | GitHub board harvesting |
| `collect_kicad.sh` | File flattening |

## Design Principles

- **Trace is the atom of design knowledge** — not the board, not the component
- **Pads only in the image** — traces are the variable, pads are the landmarks
- **Scale distortion sorting** — traces closest to 1.0 scale rank highest
- **Quality over quantity** — 60% rejection rate during collection
- **Layer indices not names** — portable across boards with different naming
- **Clearance validation with rollback** — zero DRC violations guaranteed
- **ChromaDB for speed** — millisecond queries at any database size
- **n=200 recommended** — wider candidate pool finds better scale matches



Note: 09-Apr-2026
The system is working really well now. To summarise what got us here:

Full pattern renderer — traces + pads giving much richer embeddings
Rotation normalisation — src→dst always on +X axis
Pad snap fix in rebuild_routes.py — eliminates floating point stub artefacts
0.5mm stub filter — discards routes with trivial F.Cu endpoints
Obstacle-descending route order — hardest routes get first pick
R2 optimiser — 8-direction snapping for clean human-like geometry