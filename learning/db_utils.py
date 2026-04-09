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


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 db_utils.py <db_path> [board_name]")
        sys.exit(1)

    db_path = sys.argv[1]
    col = open_collection(db_path)
    print(f"Collection: {col.count()} patterns")

    if len(sys.argv) >= 3:
        board_name = sys.argv[2]
        exists = board_exists(col, board_name)
        count = board_count(col, board_name)
        print(f"Board '{board_name}': exists={exists}, count={count}")
    else:
        boards = board_list(col)
        print(f"Boards: {len(boards)}")
        for name, count in list(boards.items())[:20]:
            print(f"  {name:50s} {count}")