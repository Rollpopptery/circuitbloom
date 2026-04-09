#!/usr/bin/env python3
"""
trace_patterndb.py — DINOv2-encode full patterns (pads + traces) and store
in ChromaDB for trace pattern matching.

Uses full_pattern_render.py for image rendering — pads and existing traces
are both encoded, giving the model richer context about routing congestion.

Uses DINOv2-small for visual encoding (384-dim embeddings).

For each atomic route (pad-to-pad, pad-to-via, via-to-pad):
  1. Choose consistent source/dest (higher pin count = source)
  2. Render full pattern image — pads + all other-net traces in window
  3. Encode with DINOv2 → 384-dim vector
  4. Store in ChromaDB with trace segments as payload

At query time, tracks already placed on the board are included in the
render, so each query reflects the current routing state.

Usage:
    from trace_patterndb import index_trace_patterns, search_patterns

    # Index from live board
    collection = index_trace_patterns(board_name="hackrf-one")

    # Search for similar patterns
    results = search_patterns(collection, pads, tracks, "NET1", (0, 0), (5, 3))
"""

import json
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from rebuild_routes import rebuild_routes
from full_pattern_render import (
    render_full_pattern,
    get_source_dest_in_grid,
    count_component_pins,
    count_visible_obstacles,
    choose_source_dest,
)

SERVER = "http://localhost:8084"


# ============================================================
# DINOv2 ENCODER
# ============================================================

_dino_model = None
_dino_processor = None


def _load_dino():
    """Load DINOv2-small model (lazy, cached)."""
    global _dino_model, _dino_processor
    if _dino_model is not None:
        return _dino_model, _dino_processor

    try:
        from transformers import AutoImageProcessor, AutoModel
        processor = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        model = AutoModel.from_pretrained("facebook/dinov2-small")
        model.eval()
        _dino_model = model
        _dino_processor = processor
        print(f"  DINOv2-small loaded (384-dim embeddings)")
        return model, processor
    except ImportError:
        raise ImportError(
            "DINOv2 requires transformers and torch. Install with: "
            "pip install transformers torch"
        )


def encode_image(img):
    """Encode a PIL image to a vector using DINOv2.

    Args:
        img: PIL Image (224x224 RGB)

    Returns:
        list of 384 floats (normalised DINOv2 CLS embedding)
    """
    import torch

    model, processor = _load_dino()

    inputs = processor(images=img, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
        cls = outputs.last_hidden_state[:, 0, :]
        cls = cls / cls.norm(dim=-1, keepdim=True)
        return cls[0].numpy().tolist()


# ============================================================
# INDEXING
# ============================================================

_doc_counter = 0


def index_trace_patterns(collection_name="trace_patterns",
                         persist_dir=None,
                         append=False,
                         board_name=""):
    """Fetch board state, rebuild routes, render full patterns, encode with DINOv2,
    and index into ChromaDB.

    Only indexes traces that start and end on layer 0 (F.Cu) — the foundation
    class for SMD pad-to-pad routing. Via transitions through other layers are
    permitted as long as endpoints are on F.Cu.

    Each route is rendered with all other-net traces visible in the window,
    capturing the routing congestion context at the time the route was made.

    Args:
        collection_name: ChromaDB collection name
        persist_dir: path for persistent storage
        append: if True, add to existing; if False, replace
        board_name: board identifier

    Returns:
        ChromaDB collection
    """
    global _doc_counter

    if persist_dir is None:
        persist_dir = os.path.join(os.path.dirname(__file__),
                                   "trace_pattern_collection")

    # Fetch board state
    print(f"Fetching board state{f' ({board_name})' if board_name else ''}...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    tracks = data.get("tracks", [])
    pads = data.get("pads", [])
    vias = data.get("vias", [])
    print(f"  {len(tracks)} tracks, {len(pads)} pads, {len(vias)} vias")

    # Get copper layer order from board state (authoritative, from KiCad)
    board = data.get("board", {})
    copper_layers = board.get("copper_layers", [])

    if copper_layers:
        layer_to_index = {name: idx for idx, name in enumerate(copper_layers)}
        n_layers = len(copper_layers)
    else:
        print("  WARNING: no copper_layers in board state")
        layer_to_index = {}
        n_layers = 0

    print(f"  Layers ({n_layers}): {layer_to_index}")

    # Build pad pin counts for source selection
    pad_counts = count_component_pins(pads)

    # Rebuild routes
    routes = rebuild_routes(tracks, pads, vias)
    print(f"  {len(routes)} routes")

    # Filter to routes with pad endpoints
    valid_routes = [r for r in routes
                    if r.get("from_pad") or r.get("to_pad")]
    print(f"  {len(valid_routes)} routes with pad endpoints")

    # Load DINOv2
    _load_dino()

    # Build documents
    ids = []
    embeddings = []
    metadatas = []
    skipped = 0

    for route in valid_routes:
        _doc_counter += 1

        net = route["net"]

        # Consistent source/dest selection
        src_pad, dst_pad = choose_source_dest(
            route.get("from_pad"), route.get("to_pad"), pad_counts
        )

        # Get source and dest points
        if src_pad:
            source = (src_pad["x"], src_pad["y"]) if "x" in src_pad else route["from_pt"]
        else:
            source = route["from_pt"]
        if dst_pad:
            dest = (dst_pad["x"], dst_pad["y"]) if "x" in dst_pad else route["to_pt"]
        else:
            dest = route["to_pt"]

        # Skip short routes
        trace_len = math.hypot(dest[0] - source[0], dest[1] - source[1])
        if trace_len < 2.0:
            skipped += 1
            continue

        # Skip routes with no meaningful obstacle pattern
        n_obstacles = count_visible_obstacles(pads, net, source, dest)
        if n_obstacles < 3:
            skipped += 1
            continue

        # Skip single-segment traces — straight lines have no routing knowledge
        if len(route["segments"]) < 2:
            skipped += 1
            continue

        first_seg = route["segments"][0]        
        last_seg = route["segments"][-1]
        first_layer = layer_to_index.get(first_seg["layer"], -1)
        last_layer = layer_to_index.get(last_seg["layer"], -1)
        if first_layer != 0 or last_layer != 0:
            skipped += 1
            continue

        # Reject traces with tiny F.Cu stubs — minimum 0.5mm on surface layer
        import math as _math
        first_len = _math.hypot(first_seg["x2"]-first_seg["x1"], first_seg["y2"]-first_seg["y1"])
        last_len = _math.hypot(last_seg["x2"]-last_seg["x1"], last_seg["y2"]-last_seg["y1"])
        if first_len < 0.5 or last_len < 0.5:
            skipped += 1
            continue

        # Render full pattern — pads + all other-net traces in the window
        # Exclude this route's own net so the pattern shows only the
        # obstacle context, not the route itself
        other_tracks = [t for t in tracks if t.get("net") != net]
        img = render_full_pattern(pads, other_tracks, net, source, dest)
        vec = encode_image(img)

        # Grid positions
        sgx, sgy, dgx, dgy = get_source_dest_in_grid(source, dest)

        # Metadata — use consistent source/dest
        from_ref = src_pad["ref"] if src_pad else ""
        from_pin = src_pad["pin"] if src_pad else ""
        to_ref = dst_pad["ref"] if dst_pad else ""
        to_pin = dst_pad["pin"] if dst_pad else ""

        metadata = {
            "board": board_name,
            "net": net,
            "type": route["type"],
            "length_mm": round(route["length_mm"], 3),
            "layers": ",".join(
                str(layer_to_index.get(l, 0)) for l in route["layers"]
            ),
            "n_vias": len(route["vias"]),
            "n_segments": len(route["segments"]),
            "from_ref": from_ref,
            "from_pin": from_pin,
            "to_ref": to_ref,
            "to_pin": to_pin,
            "from_x": round(source[0], 2),
            "from_y": round(source[1], 2),
            "to_x": round(dest[0], 2),
            "to_y": round(dest[1], 2),
            "src_gx": sgx,
            "src_gy": sgy,
            "dst_gx": dgx,
            "dst_gy": dgy,
            "trace_len_mm": round(trace_len, 2),
            "n_layers": n_layers,
            "segments": json.dumps(
                [[round(s["x1"], 2), round(s["y1"], 2),
                  round(s["x2"], 2), round(s["y2"], 2),
                  layer_to_index.get(s["layer"], 0)] for s in route["segments"]]
            ),
            "vias": json.dumps(
                [[round(v["x"], 2), round(v["y"], 2)]
                 for v in route["vias"]]
            ),
        }

        # Unique ID
        from_id = (f"{from_ref}.{from_pin}" if from_ref
                   else f"{source[0]:.1f}_{source[1]:.1f}")
        to_id = (f"{to_ref}.{to_pin}" if to_ref
                 else f"{dest[0]:.1f}_{dest[1]:.1f}")
        prefix = f"{board_name}__" if board_name else ""
        doc_id = f"{prefix}{net}__{from_id}__{to_id}__{_doc_counter}"

        ids.append(doc_id)
        embeddings.append(vec)
        metadatas.append(metadata)

    print(f"  {len(ids)} patterns built ({skipped} skipped)")

    # Store in ChromaDB
    import chromadb

    client = chromadb.PersistentClient(path=persist_dir)

    if append:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "PCB trace patterns — DINOv2-encoded full patterns, F.Cu endpoints only",
                       "hnsw:space": "cosine"}
        )
    else:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        collection = client.create_collection(
            name=collection_name,
            metadata={"description": "PCB trace patterns — DINOv2-encoded full patterns, F.Cu endpoints only",
                       "hnsw:space": "cosine"}
        )

    # Batch insert
    batch_size = 5000
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i:i + batch_size],
            embeddings=embeddings[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )

    print(f"  Indexed {len(ids)} patterns (collection total: {collection.count()})")
    return collection


# ============================================================
# SEARCH
# ============================================================

def search_patterns(collection, pads, tracks, route_net, source_pt, dest_pt,
                    n=20, where=None):
    """Search for similar full patterns (pads + traces).

    Args:
        collection: ChromaDB collection
        pads: list of pad dicts from board state
        tracks: list of track dicts already placed on board
        route_net: net being routed
        source_pt: (x, y) source in mm (already chosen via choose_source_dest)
        dest_pt: (x, y) destination in mm
        n: number of results
        where: optional ChromaDB filter

    Returns:
        list of (metadata, distance) tuples
    """
    # Exclude the route's own net — same as collection time
    other_tracks = [t for t in tracks if t.get("net") != route_net]

    img = render_full_pattern(pads, other_tracks, route_net, source_pt, dest_pt)
    vec = encode_image(img)

    kwargs = {
        "query_embeddings": [vec],
        "n_results": n,
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    out = []
    for i in range(len(results["ids"][0])):
        out.append((
            results["metadatas"][0][i],
            results["distances"][0][i],
        ))
    return out


def print_results(results):
    """Pretty-print search results."""
    for i, (meta, dist) in enumerate(results):
        board = f" [{meta['board']}]" if meta.get("board") else ""
        print(f"\n  [{i + 1}] (dist={dist:.4f}){board}")
        print(f"      {meta['net']}  {meta['from_ref']}.{meta['from_pin']} -> "
              f"{meta['to_ref']}.{meta['to_pin']}")
        print(f"      {meta['length_mm']}mm  {meta['layers']}  "
              f"vias={meta['n_vias']}  segs={meta['n_segments']}")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trace pattern database")
    parser.add_argument("action", choices=["index"],
                        help="Action to perform")
    parser.add_argument("--board", default="",
                        help="Board name for indexing")
    parser.add_argument("--db", default=None,
                        help="ChromaDB persist directory")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing collection")
    args = parser.parse_args()

    if args.action == "index":
        collection = index_trace_patterns(
            persist_dir=args.db,
            append=args.append,
            board_name=args.board,
        )