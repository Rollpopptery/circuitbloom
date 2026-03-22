"""
component_repulsion.py — Physical repulsion between components for dpcb_api.

For each component, computes a repulsion vector from ALL other components
based on proximity and size. Closer components push harder. Larger
components push harder. This is the missing "spatial awareness" force
that prevents component overlap and creates balanced spacing.

Physics model:
    For each pair (A, B):
        - direction: unit vector from B center to A center (pushes A away from B)
        - size factor: (radius_A + radius_B) — larger components push harder
        - distance factor: 1 / distance² (Coulomb-like falloff)
        - force on A from B: direction * size_factor / distance²

    Component radius = half the diagonal of the pad bounding box.
    This captures both component size and shape.

Usage:
    from component_repulsion import compute_component_repulsion
    from component_repulsion import compute_component_repulsion_all
    from component_repulsion import format_component_repulsion
    from component_repulsion import format_component_repulsion_all

    result = compute_component_repulsion(board, "R1")
    print(format_component_repulsion(result))
"""

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComponentPush:
    """Repulsion contribution from one other component."""
    ref: str
    fx: float
    fy: float
    distance: float


@dataclass
class ComponentRepulsionResult:
    ref: str
    x: float
    y: float
    radius: float
    fx: float
    fy: float
    magnitude: float
    push_toward_x: float
    push_toward_y: float
    contributor_count: int
    top_contributors: list = field(default_factory=list)


def _component_radius(fp):
    """Compute component radius from pad bounding box diagonal."""
    if not fp.abs_pads:
        return 0.5  # default for components with no pads

    min_x = min(p.x for p in fp.abs_pads)
    max_x = max(p.x for p in fp.abs_pads)
    min_y = min(p.y for p in fp.abs_pads)
    max_y = max(p.y for p in fp.abs_pads)

    width = max_x - min_x
    height = max_y - min_y

    # Radius = half diagonal, with a minimum for point-like components
    diagonal = math.sqrt(width * width + height * height)
    return max(0.5, diagonal / 2.0)


def _component_center(fp):
    """Component center from footprint position."""
    return (fp.x, fp.y)


def compute_component_repulsion(board, ref):
    """Compute physical repulsion on one component from all others."""
    # Find target footprint
    target = None
    for f in board.footprints:
        if f.ref == ref:
            target = f
            break
    if target is None:
        return None

    ax, ay = _component_center(target)
    ra = _component_radius(target)

    total_fx = 0.0
    total_fy = 0.0
    contributors = []

    for fp in board.footprints:
        if fp.ref == ref:
            continue

        bx, by = _component_center(fp)
        rb = _component_radius(fp)

        dx = ax - bx
        dy = ay - by
        dist = math.sqrt(dx * dx + dy * dy)

        if dist < 0.01:
            # Coincident — push in arbitrary direction
            dist = 0.01
            dx, dy = 1.0, 0.0

        # Normalize direction
        ux = dx / dist
        uy = dy / dist

        # Coulomb-like: size_factor / distance²
        size_factor = ra + rb
        force_mag = size_factor / (dist * dist)

        fx = ux * force_mag
        fy = uy * force_mag

        total_fx += fx
        total_fy += fy
        contributors.append(ComponentPush(
            ref=fp.ref,
            fx=round(fx, 4),
            fy=round(fy, 4),
            distance=round(dist, 2),
        ))

    magnitude = math.sqrt(total_fx * total_fx + total_fy * total_fy)

    # Sort contributors by force magnitude descending
    contributors.sort(key=lambda c: -(c.fx * c.fx + c.fy * c.fy))

    return ComponentRepulsionResult(
        ref=target.ref,
        x=round(ax, 2),
        y=round(ay, 2),
        radius=round(ra, 2),
        fx=round(total_fx, 2),
        fy=round(total_fy, 2),
        magnitude=round(magnitude, 2),
        push_toward_x=round(ax + total_fx, 2),
        push_toward_y=round(ay + total_fy, 2),
        contributor_count=len(contributors),
        top_contributors=contributors[:5],
    )


def compute_component_repulsion_all(board):
    """Compute physical repulsion for all components."""
    results = []
    for fp in board.footprints:
        r = compute_component_repulsion(board, fp.ref)
        if r:
            results.append(r)
    results.sort(key=lambda r: -r.magnitude)
    return results


def format_component_repulsion(result):
    if result is None:
        return "ERR: component not found"

    lines = [
        f"OK: {result.ref} ({result.x},{result.y})"
        f"  radius={result.radius}mm"
        f"  repulsion=({result.fx},{result.fy})"
        f"  mag={result.magnitude}mm"
        f"  push_toward=({result.push_toward_x},{result.push_toward_y})"
        f"  from {result.contributor_count} component(s)"
    ]
    for c in result.top_contributors:
        cmag = math.sqrt(c.fx * c.fx + c.fy * c.fy)
        lines.append(
            f"  {c.ref}: ({c.fx},{c.fy}) dist={c.distance}mm")

    return "\n".join(lines)


def format_component_repulsion_all(results):
    if not results:
        return "OK: no components"

    lines = [f"OK: {len(results)} component(s)"]
    for r in results:
        lines.append(
            f"  {r.ref:4s} ({r.x},{r.y})"
            f"  r={r.radius}mm"
            f"  push=({r.fx},{r.fy})"
            f"  mag={r.magnitude}mm")

    return "\n".join(lines)
