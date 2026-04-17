#!/usr/bin/env python3
"""
db_utils.py — ChromaDB utility functions for the trace pattern database.

Usage:
    from db_utils import open_collection, board_exists, board_count, board_list

    collection = open_collection("./ROUTE_PATTERNS_FULL4")
    if not board_exists(collection, "hackrf-one"):
        index_trace_patterns(...)
"""

import chromadb
import argparse


def open_collection(db_path, collection_name="trace_patterns"):
    """Open an existing ChromaDB collection.

    Args:
        db_path: path to ChromaDB persist directory
        collection_name: collection name (default: trace_patterns)

    Returns:
        ChromaDB collection

    Raises:
        Exception if collection does not exist
    """
    client = chromadb.PersistentClient(path=db_path)
    return client.get_collection(collection_name)


def board_exists(collection, board_name):
    """Check if a board has any patterns in the collection.

    Args:
        collection: ChromaDB collection
        board_name: board name string (as stored in metadata "board" field)

    Returns:
        True if at least one pattern from this board exists
    """
    try:
        result = collection.get(
            where={"board": {"$eq": board_name}},
            limit=1,
            include=[]
        )
        return len(result["ids"]) > 0
    except Exception:
        return False


def board_count(collection, board_name):
    """Count patterns from a specific board.

    Args:
        collection: ChromaDB collection
        board_name: board name string

    Returns:
        int — number of patterns from this board
    """
    try:
        result = collection.get(
            where={"board": {"$eq": board_name}},
            include=[]
        )
        return len(result["ids"])
    except Exception:
        return 0


def board_list(collection):
    """List all boards in the collection with their pattern counts.

    Args:
        collection: ChromaDB collection

    Returns:
        dict of board_name -> pattern_count, sorted by count descending
    """
    total = collection.count()
    if total == 0:
        return {}

    counts = {}
    batch_size = 5000
    offset = 0

    while offset < total:
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"]
        )
        for meta in result["metadatas"]:
            board = meta.get("board", "")
            counts[board] = counts.get(board, 0) + 1
        offset += len(result["ids"])

    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def delete_board(collection, board_name):
    """Delete all patterns from a specific board.

    Args:
        collection: ChromaDB collection
        board_name: board name string

    Returns:
        int — number of patterns deleted
    """
    try:
        result = collection.get(
            where={"board": {"$eq": board_name}},
            include=[]
        )
        ids = result["ids"]
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception:
        return 0
    

def collection_stats(collection):
    """Print statistics about the collection contents.

    Breaks down patterns by layer count and via count.

    Args:
        collection: ChromaDB collection

    Returns:
        dict with stats
    """
    total = collection.count()
    if total == 0:
        return {}

    single_layer = 0
    multi_layer  = 0
    via_counts   = {}
    layer_counts = {}

    batch_size = 5000
    offset     = 0

    while offset < total:
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["metadatas"]
        )
        for meta in result["metadatas"]:
            n_vias   = meta.get("n_vias", 0)
            layers   = meta.get("layers", "0")
            n_layers = len(set(layers.split(",")))

            if n_layers == 1:
                single_layer += 1
            else:
                multi_layer += 1

            via_counts[n_vias]     = via_counts.get(n_vias, 0) + 1
            layer_counts[n_layers] = layer_counts.get(n_layers, 0) + 1

        offset += len(result["ids"])

    stats = {
        "total":        total,
        "single_layer": single_layer,
        "multi_layer":  multi_layer,
        "by_via_count": dict(sorted(via_counts.items())),
        "by_layer_count": dict(sorted(layer_counts.items())),
    }

    print(f"Total patterns : {total}")
    print(f"Single layer   : {single_layer} ({100*single_layer//total}%)")
    print(f"Multi layer    : {multi_layer}  ({100*multi_layer//total}%)")
    print()
    print("By via count:")
    for k, v in stats["by_via_count"].items():
        print(f"  {k} vias: {v}")
    print()
    print("By layer count:")
    for k, v in stats["by_layer_count"].items():
        print(f"  {k} layers: {v}")

    return stats

if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(description="ChromaDB collection utilities")
    parser.add_argument("db_path",               help="ChromaDB persist directory")
    parser.add_argument("board_name", nargs="?", help="Board name to query")
    parser.add_argument("--stats", action="store_true",
                        help="Show collection statistics")
    args = parser.parse_args()

    col = open_collection(args.db_path)
    print(f"Collection: {col.count()} patterns")

    if args.stats:
        collection_stats(col)
    elif args.board_name:
        exists = board_exists(col, args.board_name)
        count  = board_count(col, args.board_name)
        print(f"Board '{args.board_name}': exists={exists}, count={count}")
    else:
        boards = board_list(col)
        print(f"Boards: {len(boards)}")
        for name, count in list(boards.items())[:20]:
            print(f"  {name:50s} {count}")