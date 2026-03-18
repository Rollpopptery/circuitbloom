"""
dpcb_parser.py
Shared parsing and geometry utilities for all check_dpcb checks.

Exports:
    parse_dpcb(path)              -> fps, pads_lib, nets, tracks
    compute_pad_positions(...)    -> {(ref, pad): (ax, ay, net)}
    pt_seg_dist(px, py, x1,y1,x2,y2) -> float
    rotate_pad(dx, dy, rot)       -> (rdx, rdy)
"""

import re
import math


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_dpcb(path):
    """
    Parse a .dpcb file.

    Returns:
        fps       : {ref: {lib, x, y, rot}}
        pads_lib  : {lib:fp: {pad_num: (dx, dy)}}
        nets      : {net_name: [(ref, pad_num), ...]}
        tracks    : [(x1, y1, x2, y2, width, layer, net), ...]
        vias      : [(x, y, drill, annular, net), ...]
    """
    fps = {}
    pads_lib = {}
    nets = {}
    tracks = []
    vias = []

    with open(path) as f:
        for line in f:
            line = line.split('#')[0].strip()
            if not line:
                continue

            # FP:U1:Package_DIP:DIP-8_W7.62mm@(140,80)[:r180][:layer]
            m = re.match(r'FP:(\w+):([^@]+)@\(([^)]+)\)(?::r(\d+))?', line)
            if m:
                ref, lib_fp, xy, rot = m.groups()
                x, y = map(float, xy.split(','))
                fps[ref] = {'lib': lib_fp, 'x': x, 'y': y, 'rot': int(rot or 0)}
                continue

            # PADS:lib:fp:1@(dx,dy),...
            m = re.match(r'PADS:(.+?):([\d]+@\([^)]+\)(?:,[\d]+@\([^)]+\))*)', line)
            if m:
                lib_fp = m.group(1)
                pad_entries = m.group(2)
                pads_lib[lib_fp] = {}
                for pe in re.finditer(r'(\d+)@\(([^)]+)\)', pad_entries):
                    pnum = pe.group(1)
                    dx, dy = map(float, pe.group(2).split(','))
                    pads_lib[lib_fp][pnum] = (dx, dy)
                continue

            # NET:name:R1.1,U1.3
            m = re.match(r'NET:([^:]+):(.+)', line)
            if m:
                net_name = m.group(1)
                pads = [p.strip() for p in m.group(2).split(',')]
                nets[net_name] = [
                    (p.split('.')[0], p.split('.')[1]) for p in pads
                ]
                continue

            # TRK:(x1,y1)->(x2,y2):width:layer:net
            m = re.match(r'TRK:\(([^)]+)\)->\(([^)]+)\):([^:]+):([^:]+):(.+)', line)
            if m:
                x1, y1 = map(float, m.group(1).split(','))
                x2, y2 = map(float, m.group(2).split(','))
                width = float(m.group(3))
                layer = m.group(4).strip()
                net = m.group(5).strip()
                tracks.append((x1, y1, x2, y2, width, layer, net))
                continue

            # VIA:(x,y):drill/annular:net
            m = re.match(r'VIA:\(([^)]+)\):([^:]+):(.+)', line)
            if m:
                x, y = map(float, m.group(1).split(','))
                drill_ann = m.group(2).strip().split('/')
                drill = float(drill_ann[0])
                annular = float(drill_ann[1]) if len(drill_ann) > 1 else 0.0
                net = m.group(3).strip()
                vias.append((x, y, drill, annular, net))
                continue

    return fps, pads_lib, nets, tracks, vias


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def rotate_pad(dx, dy, rot):
    """Apply footprint rotation to a pad offset. rot in {0, 90, 180, 270}.
    Verified against KiCad 9.0.7 DRC output 2026-03-09."""
    if rot == 0:   return dx, dy
    if rot == 90:  return dy, -dx
    if rot == 180: return -dx, -dy
    if rot == 270: return -dy, dx
    return dx, dy


def compute_pad_positions(fps, pads_lib, nets):
    """
    Compute absolute pad positions for all pads referenced in nets.

    Returns:
        {(ref, pad_num): (abs_x, abs_y, net_name)}
    """
    pad_net = {}
    for net_name, pad_list in nets.items():
        for ref, pnum in pad_list:
            pad_net[(ref, pnum)] = net_name

    positions = {}
    warnings = []
    for ref, fp in fps.items():
        lib_fp = fp['lib']
        if lib_fp not in pads_lib:
            warnings.append(f"WARNING: No PADS entry for {lib_fp} (ref={ref})")
            continue
        for pnum, (dx, dy) in pads_lib[lib_fp].items():
            rdx, rdy = rotate_pad(dx, dy, fp['rot'])
            ax = fp['x'] + rdx
            ay = fp['y'] + rdy
            net = pad_net.get((ref, pnum), 'UNKNOWN')
            positions[(ref, pnum)] = (ax, ay, net)

    return positions, warnings


def pt_seg_dist(px, py, x1, y1, x2, y2):
    """Minimum distance from point (px, py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - cx, py - cy)
