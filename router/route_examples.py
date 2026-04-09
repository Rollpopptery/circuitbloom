#!/usr/bin/env python3
"""
route_examples.py — Search the route examples ChromaDB collection.

Provides a clean interface for querying the route examples database
built by collect_all_boards.py.

Usage:
    from route_examples import search, is_available

    if is_available():
        results = search("crystal oscillator load capacitor", n=5)
"""

import os

_collection = None
_db_path = os.path.join(os.path.dirname(__file__), "..", "learning", "route_collection")


def is_available():
    """Check if the route examples database exists."""
    return os.path.isdir(_db_path)


def _get_collection():
    """Lazy-load the ChromaDB collection."""
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        if not os.path.isdir(_db_path):
            return None
        client = chromadb.PersistentClient(path=_db_path)
        _collection = client.get_collection("pcb_routes")
        return _collection
    except Exception:
        return None


def search(query, n=5, board=None):
    """Search route examples by natural language query.

    Args:
        query: search text (e.g. "USB differential pair", "decoupling capacitor")
        n: number of results (default 5, max 20)
        board: optional board name filter

    Returns:
        dict with ok, query, count, routes[] — each route has description, metadata, distance
    """
    collection = _get_collection()
    if collection is None:
        return {"ok": False, "error": "route_collection not available — run collect_all_boards.py first"}
    if not query:
        return {"ok": False, "error": "empty query"}

    kwargs = {"query_texts": [query], "n_results": min(n, 20)}
    if board:
        kwargs["where"] = {"board": board}

    try:
        results = collection.query(**kwargs)
    except Exception:
        # Collection may have been rebuilt — reset cache and retry
        global _collection
        _collection = None
        collection = _get_collection()
        if collection is None:
            return {"ok": False, "error": "route_collection query failed"}
        results = collection.query(**kwargs)

    routes = []
    for i in range(len(results["ids"][0])):
        routes.append({
            "description": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": round(results["distances"][0][i], 4),
        })

    return {"ok": True, "query": query, "count": len(routes), "routes": routes}
