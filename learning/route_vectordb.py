#!/usr/bin/env python3
"""
route_vectordb.py — Index rebuilt routes into ChromaDB for semantic search.

Builds a text description for each route (net, endpoints, component info,
layers, length, vias) and stores the full route data as metadata.

Usage:
    from route_vectordb import index_routes, search_routes

    # Build and index from live board (replaces existing)
    db = index_routes()

    # Append to existing collection
    db = index_routes(append=True)

    # Index a specific board by name
    db = index_routes(board_name="hackrf-one", append=True)

    # Search
    results = search_routes(db, "power supply decoupling capacitor")
    results = search_routes(db, "microcontroller GND connection")
"""

import json
import sys
import os
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))

from rebuild_routes import rebuild_routes
from component_info import ComponentInfo
from grab_layer import find_socket

import chromadb

SERVER = "http://localhost:8084"


def _pad_description(pad_info, comp_info):
    """Build a text description for a pad endpoint."""
    if not pad_info:
        return None
    ref = pad_info["ref"]
    pin = pad_info["pin"]
    comp = comp_info.component(ref) if comp_info else None
    if comp:
        fp = str(comp["footprint"]).split(":")[-1]
        return f"{ref} pin {pin} ({comp['value']}, {fp})"
    return f"{ref} pin {pin}"


def _junction_description(point, vias):
    """Build a text description for a trace junction endpoint."""
    x, y = point
    if vias:
        return f"via junction at ({x:.1f}, {y:.1f})"
    return f"trace junction at ({x:.1f}, {y:.1f})"


_doc_counter = 0

def build_route_document(route, comp_info, board_name=""):
    """Build a description string and metadata dict for one route.

    Returns:
        (doc_id, description, metadata)
    """
    global _doc_counter
    _doc_counter += 1
    net = route["net"]
    rtype = route["type"]
    length = route["length_mm"]
    layers = route["layers"]
    n_vias = len(route["vias"])

    # From endpoint
    from_desc = _pad_description(route["from_pad"], comp_info)
    if not from_desc:
        from_desc = _junction_description(route["from_pt"], route["vias"])

    # To endpoint
    to_desc = _pad_description(route["to_pad"], comp_info)
    if not to_desc:
        to_desc = _junction_description(route["to_pt"], route["vias"])

    # Build description text for embedding
    parts = []
    if board_name:
        parts.append(f"{board_name} board")
    parts.append(f"{net} net")

    if rtype == "pin_to_pin":
        parts.append(f"route from {from_desc} to {to_desc}")
    else:
        parts.append(f"branch from {from_desc} to {to_desc}")

    parts.append(f"{length:.1f}mm on {', '.join(layers)}")

    if n_vias:
        parts.append(f"with {n_vias} via{'s' if n_vias > 1 else ''}")

    description = ". ".join(parts)

    # Metadata — ChromaDB metadata values must be str, int, float, or bool
    metadata = {
        "board": board_name,
        "net": net,
        "type": rtype,
        "length_mm": round(length, 3),
        "layers": ",".join(layers),
        "n_vias": n_vias,
        "n_segments": len(route["segments"]),
        "from_ref": route["from_pad"]["ref"] if route["from_pad"] else "",
        "from_pin": route["from_pad"]["pin"] if route["from_pad"] else "",
        "to_ref": route["to_pad"]["ref"] if route["to_pad"] else "",
        "to_pin": route["to_pad"]["pin"] if route["to_pad"] else "",
        "from_x": round(route["from_pt"][0], 2),
        "from_y": round(route["from_pt"][1], 2),
        "to_x": round(route["to_pt"][0], 2),
        "to_y": round(route["to_pt"][1], 2),
        "segments": json.dumps([[round(s["x1"], 2), round(s["y1"], 2),
                                  round(s["x2"], 2), round(s["y2"], 2),
                                  s["layer"]] for s in route["segments"]]),
        "vias": json.dumps([[round(v["x"], 2), round(v["y"], 2)]
                            for v in route["vias"]]),
    }

    # Add component info to metadata if available
    if route["from_pad"] and comp_info:
        comp = comp_info.component(route["from_pad"]["ref"])
        if comp:
            metadata["from_value"] = comp["value"]
            metadata["from_footprint"] = str(comp["footprint"])
    if route["to_pad"] and comp_info:
        comp = comp_info.component(route["to_pad"]["ref"])
        if comp:
            metadata["to_value"] = comp["value"]
            metadata["to_footprint"] = str(comp["footprint"])

    # Unique ID — prefixed with board name, suffixed with counter to avoid collisions
    from_id = f"{route['from_pad']['ref']}.{route['from_pad']['pin']}" if route["from_pad"] else f"{route['from_pt'][0]:.1f}_{route['from_pt'][1]:.1f}"
    to_id = f"{route['to_pad']['ref']}.{route['to_pad']['pin']}" if route["to_pad"] else f"{route['to_pt'][0]:.1f}_{route['to_pt'][1]:.1f}"
    prefix = f"{board_name}__" if board_name else ""
    doc_id = f"{prefix}{net}__{from_id}__{to_id}__{_doc_counter}"

    return doc_id, description, metadata


def index_routes(collection_name="pcb_routes",
                 persist_dir=os.path.join(os.path.dirname(__file__), "route_collection"),
                 append=False, board_name=""):
    """Fetch board state, rebuild routes, and index into ChromaDB.

    Args:
        collection_name: ChromaDB collection name
        persist_dir: path for persistent storage (None = in-memory)
        append: if True, add to existing collection; if False, replace it
        board_name: board identifier stored in metadata and doc IDs

    Returns:
        ChromaDB collection
    """
    # Fetch board state
    print(f"Fetching board state{f' ({board_name})' if board_name else ''}...")
    data = json.loads(urllib.request.urlopen(SERVER + "/").read())
    tracks = data.get("tracks", [])
    pads = data.get("pads", [])
    vias = data.get("vias", [])
    print(f"  {len(tracks)} tracks, {len(pads)} pads, {len(vias)} vias")

    # Component info from KiCad
    comp_info = None
    sock = find_socket()
    if sock:
        try:
            comp_info = ComponentInfo(sock)
        except Exception as e:
            print(f"  Warning: component info unavailable: {e}")

    # Rebuild routes
    routes = rebuild_routes(tracks, pads, vias)
    print(f"  {len(routes)} routes")

    # Build documents
    ids = []
    documents = []
    metadatas = []
    for route in routes:
        doc_id, description, metadata = build_route_document(route, comp_info, board_name)
        ids.append(doc_id)
        documents.append(description)
        metadatas.append(metadata)

    # Index into ChromaDB
    if persist_dir:
        client = chromadb.PersistentClient(path=persist_dir)
    else:
        client = chromadb.Client()

    if append:
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "PCB routes with component and connectivity info"}
        )
    else:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        collection = client.create_collection(
            name=collection_name,
            metadata={"description": "PCB routes with component and connectivity info"}
        )

    # ChromaDB batch limit is 5461
    batch_size = 5000
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i:i + batch_size],
            documents=documents[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )

    print(f"  Indexed {len(routes)} routes (collection total: {collection.count()})")
    return collection


def search_routes(collection, query, n=5, where=None):
    """Search routes by natural language query.

    Args:
        collection: ChromaDB collection from index_routes()
        query: search text
        n: number of results
        where: optional ChromaDB where filter, e.g. {"net": "GND"}
               or {"board": "hackrf-one"}

    Returns:
        list of (description, metadata, distance) tuples
    """
    kwargs = {"query_texts": [query], "n_results": n}
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    out = []
    for i in range(len(results["ids"][0])):
        out.append((
            results["documents"][0][i],
            results["metadatas"][0][i],
            results["distances"][0][i],
        ))
    return out


def print_results(results):
    """Pretty-print search results."""
    for i, (desc, meta, dist) in enumerate(results):
        board = f" [{meta['board']}]" if meta.get("board") else ""
        print(f"\n  [{i+1}] (dist={dist:.3f}){board}")
        print(f"      {desc}")
        print(f"      {meta['type']}  {meta['length_mm']}mm  {meta['layers']}  vias={meta['n_vias']}")


if __name__ == "__main__":
    collection = index_routes()

    queries = [
        "microcontroller ground connection",
        "USB data signal",
        "power supply decoupling",
        "encoder rotary switch",
        "LED data chain",
        "crystal oscillator",
        "SPI bus",
    ]

    for q in queries:
        print(f"\n{'=' * 60}")
        print(f"QUERY: {q}")
        print(f"{'=' * 60}")
        results = search_routes(collection, q, n=3)
        print_results(results)
