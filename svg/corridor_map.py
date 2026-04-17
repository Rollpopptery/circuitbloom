#!/usr/bin/env python3
"""
corridor_map.py — Free space decomposition into named rooms and corridors.

Uses shapely to compute exact free space geometry from pad obstacles,
then decomposes it into a small set of named rectangular rooms using
obstacle boundary cut lines and rectangle merging.

The result is a routing graph — rooms connected by shared edges —
suitable for LLM spatial reasoning and path finding.

Usage:
    from corridor_map import CorridorMap
    cmap = CorridorMap.from_state(state, board)
    print(cmap.describe())
    path = cmap.path_for_pads(pad_a, pad_b)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import Point, box
from shapely.ops import unary_union


# Clearance large enough that adjacent pads' zones touch,
# eliminating thin inter-pin slivers
CLEARANCE = 1.27  # mm — half standard pin pitch, adjacent pads merge
MIN_DIM   = 1.5   # mm minimum room dimension
MIN_AREA  = 4.0   # mm² minimum room area
MIN_FREE  = 0.5   # fraction of cell that must be free — lower to eliminate gaps


@dataclass
class Corridor:
    name:     str
    x1:       float
    y1:       float
    x2:       float
    y2:       float
    adjacent: list[str] = field(default_factory=list)
    pads:     list[str] = field(default_factory=list)

    @property
    def w(self): return round(self.x2 - self.x1, 3)

    @property
    def h(self): return round(self.y2 - self.y1, 3)

    @property
    def cx(self): return round((self.x1 + self.x2) / 2, 3)

    @property
    def cy(self): return round((self.y1 + self.y2) / 2, 3)

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def shares_edge(self, other: "Corridor") -> bool:
        tol = 0.6
        if abs(self.x2 - other.x1) < tol or abs(other.x2 - self.x1) < tol:
            return min(self.y2, other.y2) - max(self.y1, other.y1) >= MIN_DIM
        if abs(self.y2 - other.y1) < tol or abs(other.y2 - self.y1) < tol:
            return min(self.x2, other.x2) - max(self.x1, other.x1) >= MIN_DIM
        return False

    def describe(self) -> str:
        return (
            f"CORRIDOR: {self.name}\n"
            f"  bounds: x={self.x1}..{self.x2}  y={self.y1}..{self.y2}"
            f"  ({self.w:.1f}mm wide  {self.h:.1f}mm tall)\n"
            f"  adjacent: {', '.join(self.adjacent) or 'none'}\n"
            f"  bordering pads: {', '.join(self.pads[:10])}"
            f"{'...' if len(self.pads) > 10 else ''}"
        )


class CorridorMap:

    def __init__(self):
        self.corridors: list[Corridor] = []
        self.board_w = self.board_h = 0.0
        self._idx: dict[str, Corridor] = {}

    @classmethod
    def from_state(cls, state: dict, board: dict) -> "CorridorMap":
        self = cls()
        self.board_w = board.get("width",  0)
        self.board_h = board.get("height", 0)
        pads = state.get("pads", [])

        # Build free space polygon using square clearance zones
        board_rect = box(0, 0, self.board_w, self.board_h)
        obstacles = unary_union([
            box(p["x"]-CLEARANCE, p["y"]-CLEARANCE,
                p["x"]+CLEARANCE, p["y"]+CLEARANCE)
            for p in pads
        ])
        free_space = board_rect.difference(obstacles)

        from collections import defaultdict
        from shapely.geometry import LineString

        # Simplify free space
        free_space = free_space.simplify(0.1)

        # Find all true 90-degree corners
        def find_corners(ring):
            pts = list(ring.coords)[:-1]
            corners = []
            n = len(pts)
            for i in range(n):
                prev = pts[(i-1) % n]
                curr = pts[i]
                nxt  = pts[(i+1) % n]
                dx1 = round(curr[0]-prev[0], 3)
                dy1 = round(curr[1]-prev[1], 3)
                dx2 = round(nxt[0]-curr[0], 3)
                dy2 = round(nxt[1]-curr[1], 3)
                if ((dx1 != 0 and dy1 == 0 and dx2 == 0 and dy2 != 0) or
                    (dx1 == 0 and dy1 != 0 and dx2 != 0 and dy2 == 0)):
                    if dx1 != 0:
                        corners.append(('v', round(curr[0],3), round(curr[1],3)))
                    else:
                        corners.append(('h', round(curr[0],3), round(curr[1],3)))
            return corners

        corners = find_corners(free_space.exterior)
        for ring in free_space.interiors:
            corners += find_corners(ring)

        # From each corner, extend a line and collect only the new coordinate
        xs = {0.0, self.board_w}
        ys = {0.0, self.board_h}

        for ext, cx, cy in corners:
            if ext == 'v':
                xs.add(round(cx, 3))  # vertical line at this x
            else:
                ys.add(round(cy, 3))  # horizontal line at this y

        xs = sorted(xs)
        ys = sorted(ys)

        # Every free cell is a room
        cells = []
        for j in range(len(ys)-1):
            for i in range(len(xs)-1):
                cx = (xs[i]+xs[i+1])/2
                cy = (ys[j]+ys[j+1])/2
                if free_space.contains(Point(cx, cy)):
                    cells.append((xs[i], ys[j], xs[i+1], ys[j+1]))

        free_rects = cells

        # Build Corridor objects directly from Tetris fill
        corridors = [
            Corridor("", round(r[0],2), round(r[1],2), round(r[2],2), round(r[3],2))
            for r in free_rects
            if (r[2]-r[0]) >= MIN_DIM and (r[3]-r[1]) >= MIN_DIM
            and (r[2]-r[0]) * (r[3]-r[1]) >= MIN_AREA
        ]

        self._name_corridors(corridors, pads)
        self.corridors = corridors
        self._idx = {c.name: c for c in corridors}
        self._build_adjacency()
        self._tag_pads(pads)
        return self

    # ── Rectangle merging ─────────────────────────────────────────────────────

    def _merge(self, rects: list) -> list:
        """Merge adjacent rectangles sharing a full edge until stable."""
        changed = True
        while changed:
            changed = False
            merged = []
            used = set()
            for i in range(len(rects)):
                if i in used:
                    continue
                for j in range(i+1, len(rects)):
                    if j in used:
                        continue
                    m = self._try_merge(rects[i], rects[j])
                    if m:
                        merged.append(m)
                        used.add(i)
                        used.add(j)
                        changed = True
                        break
                if i not in used:
                    merged.append(rects[i])
            rects = merged
        return rects

    def _try_merge(self, a, b):
        ax1,ay1,ax2,ay2 = a
        bx1,by1,bx2,by2 = b
        tol = 0.001
        # Same y-span → horizontal merge
        if abs(ay1-by1) < tol and abs(ay2-by2) < tol:
            if abs(ax2-bx1) < tol: return (ax1,ay1,bx2,ay2)
            if abs(bx2-ax1) < tol: return (bx1,ay1,ax2,ay2)
        # Same x-span → vertical merge
        if abs(ax1-bx1) < tol and abs(ax2-bx2) < tol:
            if abs(ay2-by1) < tol: return (ax1,ay1,ax2,by2)
            if abs(by2-ay1) < tol: return (ax1,by1,ax2,ay2)
        return None

    # ── Naming ────────────────────────────────────────────────────────────────

    def _name_corridors(self, corridors: list, pads: list):
        used: dict[str, int] = {}

        def unique(name: str) -> str:
            if name not in used:
                used[name] = 0
                return name
            used[name] += 1
            return f"{name}_{used[name]}"

        for c in corridors:
            rx = c.cx / self.board_w
            ry = c.cy / self.board_h
            if c.w > self.board_w * 0.5 and c.h > self.board_h * 0.3:
                name = "main_open"
            elif ry < 0.15:
                name = "top_open"
            elif ry > 0.85:
                name = "bottom_open"
            elif rx < 0.15:
                name = "left_open"
            elif rx > 0.85:
                name = "right_open"
            elif c.w > c.h * 2.5:
                name = "h_channel"
            elif c.h > c.w * 2.5:
                name = "v_channel"
            else:
                name = "open_space"
            c.name = unique(name)

    # ── Adjacency ─────────────────────────────────────────────────────────────

    def _build_adjacency(self):
        for i, a in enumerate(self.corridors):
            for j, b in enumerate(self.corridors):
                if i >= j:
                    continue
                if a.shares_edge(b):
                    if b.name not in a.adjacent:
                        a.adjacent.append(b.name)
                    if a.name not in b.adjacent:
                        b.adjacent.append(a.name)

    # ── Pad tagging ───────────────────────────────────────────────────────────

    def _tag_pads(self, pads: list):
        border = CLEARANCE + 0.5
        for p in pads:
            pid = f"{p['ref']}:{p['pin']}"
            for c in self.corridors:
                if (c.x1-border <= p["x"] <= c.x2+border and
                        c.y1-border <= p["y"] <= c.y2+border):
                    if pid not in c.pads:
                        c.pads.append(pid)

    # ── Query ─────────────────────────────────────────────────────────────────

    def corridor_for_point(self, x: float, y: float) -> Optional[Corridor]:
        """Find smallest corridor containing point, fall back to nearest."""
        best = None
        best_area = float('inf')
        for c in self.corridors:
            if c.contains(x, y):
                a = c.w * c.h
                if a < best_area:
                    best_area = a
                    best = c
        if best:
            return best
        best_dist = float('inf')
        for c in self.corridors:
            d = ((c.cx-x)**2 + (c.cy-y)**2)**0.5
            if d < best_dist:
                best_dist = d
                best = c
        return best

    def find_path(self, start: str, end: str) -> list[str]:
        if start not in self._idx or end not in self._idx:
            return []
        if start == end:
            return [start]
        visited = {start}
        queue = [[start]]
        while queue:
            path = queue.pop(0)
            for nb in self._idx[path[-1]].adjacent:
                if nb == end:
                    return path + [end]
                if nb not in visited:
                    visited.add(nb)
                    queue.append(path + [nb])
        return []

    def path_for_pads(self, pad_a: dict, pad_b: dict) -> list[str]:
        ca = self.corridor_for_point(pad_a["x"], pad_a["y"])
        cb = self.corridor_for_point(pad_b["x"], pad_b["y"])
        if not ca or not cb:
            return []
        return self.find_path(ca.name, cb.name)

    # ── Output ────────────────────────────────────────────────────────────────

    def describe(self) -> str:
        lines = ["BOARD CORRIDOR MAP", "=" * 40]
        for c in sorted(self.corridors, key=lambda c: -(c.w * c.h)):
            lines.append(c.describe())
            lines.append("")
        return "\n".join(lines)

    def describe_path(self, pad_a: dict, pad_b: dict) -> str:
        path = self.path_for_pads(pad_a, pad_b)
        if not path:
            return "no corridor path found"
        lines = [
            f"Path: {pad_a['ref']}:{pad_a['pin']} ({pad_a['x']},{pad_a['y']}) "
            f"-> {pad_b['ref']}:{pad_b['pin']} ({pad_b['x']},{pad_b['y']})",
            f"Corridors: {' -> '.join(path)}",
        ]
        for name in path:
            c = self._idx[name]
            lines.append(
                f"  {name}: x={c.x1}..{c.x2} y={c.y1}..{c.y2} "
                f"({c.w:.1f}x{c.h:.1f}mm)"
            )
        return "\n".join(lines)

    def to_json(self) -> list[dict]:
        return [
            {"name": c.name, "x1": c.x1, "y1": c.y1,
             "x2": c.x2, "y2": c.y2, "w": c.w, "h": c.h,
             "adjacent": c.adjacent, "pads": c.pads}
            for c in self.corridors
        ]