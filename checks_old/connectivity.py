"""
connectivity.py
Check 4: Net connectivity — verifies every pad in every net is reachable
via at least one track endpoint landing on the pad position.

Current approach: endpoint matching (within tol mm).
This catches unrouted pads but does NOT verify full graph connectivity
for multi-hop nets (e.g. pad A -> pad B -> pad C where A-C has no direct
track but is connected transitively). A full connected-components traversal
is a planned improvement.

TODO: Replace endpoint matching with union-find / BFS over track graph
      so multi-hop connectivity is verified properly.
"""

import math

DEFAULT_TOL = 0.01   # mm — endpoint must land within this distance of pad


def _endpoints(tracks):
    """Return list of (x, y, net) for all track endpoints."""
    pts = []
    for (x1, y1, x2, y2, w, layer, net) in tracks:
        pts.append((x1, y1, net))
        pts.append((x2, y2, net))
    return pts


def run(tracks, pad_positions, nets, tol=DEFAULT_TOL):
    """
    Args:
        tracks        : [(x1,y1,x2,y2,width,layer,net), ...]
        pad_positions : {(ref,pad): (abs_x, abs_y, net_name)}
        nets          : {net_name: [(ref, pad_num), ...]}
        tol           : endpoint-to-pad distance tolerance (mm)

    Returns:
        List of violation strings (empty = PASS).
    """
    endpoints = _endpoints(tracks)
    violations = []

    for net_name, pad_list in nets.items():
        for ref, pnum in pad_list:
            key = (ref, pnum)
            if key not in pad_positions:
                violations.append(
                    f"UNCONNECTED: {ref}.{pnum} net={net_name} "
                    f"— pad position unknown (missing PADS entry?)"
                )
                continue

            px, py, _ = pad_positions[key]
            connected = any(
                net == net_name and math.hypot(px - ex, py - ey) < tol
                for ex, ey, net in endpoints
            )
            if not connected:
                violations.append(
                    f"UNCONNECTED: {ref}.{pnum} ({px:.3f},{py:.3f}) "
                    f"net={net_name} — no track endpoint reaches this pad"
                )

    return violations
