#!/usr/bin/env python3
"""
dedup_database.py — Remove duplicate embeddings from a ChromaDB collection.

Within each board, finds patterns with identical or near-identical embeddings
and deletes all but one.

Usage:
    python3 dedup_database.py <db_path> [--threshold 0.001] [--dry-run]
"""

import argparse
import math
import sys

from db_utils import open_collection, board_list


def cosine_distance(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


def dedup_board(collection, board_name, threshold=0.001, dry_run=False):
    """Remove duplicate embeddings for one board.

    Returns (kept, deleted) counts.
    """
    result = collection.get(
        where={"board": {"$eq": board_name}},
        include=["embeddings"]
    )

    ids        = result["ids"]
    embeddings = result["embeddings"]

    if len(ids) < 2:
        return len(ids), 0

    to_delete = set()

    for i in range(len(ids)):
        if ids[i] in to_delete:
            continue
        for j in range(i + 1, len(ids)):
            if ids[j] in to_delete:
                continue
            dist = cosine_distance(embeddings[i], embeddings[j])
            if dist < threshold:
                to_delete.add(ids[j])

    if to_delete and not dry_run:
        collection.delete(ids=list(to_delete))

    return len(ids) - len(to_delete), len(to_delete)


def main():
    parser = argparse.ArgumentParser(description="Deduplicate ChromaDB embeddings per board")
    parser.add_argument("db_path",               help="ChromaDB persist directory")
    parser.add_argument("--threshold", type=float, default=0.001,
                        help="Cosine distance below which embeddings are considered identical (default: 0.001)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report duplicates without deleting")
    args = parser.parse_args()

    collection = open_collection(args.db_path)
    print(f"Collection: {collection.count()} patterns")

    if args.dry_run:
        print("Mode: DRY RUN (no deletions)")
    print()

    boards = board_list(collection)
    total_kept    = 0
    total_deleted = 0

    for board_name, count in boards.items():
        kept, deleted = dedup_board(
            collection, board_name,
            threshold=args.threshold,
            dry_run=args.dry_run,
        )
        if deleted:
            print(f"  {board_name:50s} {count} -> {kept}  (-{deleted})")
        total_kept    += kept
        total_deleted += deleted

    print()
    print(f"{'=' * 60}")
    print(f"Total deleted: {total_deleted}  remaining: {total_kept}")


if __name__ == "__main__":
    main()