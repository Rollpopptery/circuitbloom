#!/usr/bin/env python3
"""
svg_to_dsn.py — Convert BoardSVG to Specctra DSN format for FreeRouting.

Coordinate system:
    SVG uses screen coordinates (Y down, origin top-left).
    FreeRouting/Specctra DSN uses math coordinates (Y up, origin bottom-left).
    All Y values are flipped on export:  y_dsn = board_h - y_svg
    All Y values are flipped on import:  y_svg = board_h - y_ses  (dsn_to_svg.py)

Unit conversion:
    DSN header declares (resolution um 10) — 10 units per micron.
    Therefore: 1 mm = 1000 microns = 10000 DSN units.
    _um(mm) = int(round(mm * 1000))

Pad handling:
    Pad elements may be <circle>, <rect>, or <ellipse>. Centre position and
    size are extracted correctly for each tag type.
    Padstack radius = max(w, h) / 2 — circular, correctly sized per pad.
    - SMD pads (data-smd="true"):  single-layer padstack, (attach on)
    - THT pads (data-smd="false"): dual-layer padstack,   (attach off)

Keepout handling:
    Overlay polygons/rects with data-keepout="1" are emitted as (keepout)
    polygons in the (structure) block.

Layer policy:
    ALL boards have at least F.Cu and B.Cu.

No HTTP dependency. Takes a BoardSVG instance, returns a DSN string.
"""

from __future__ import annotations
from board_svg import BoardSVG, SVGElement

# DSN uses (resolution um 10) — 10 units per micron, so 1mm = 10000 units
MM_TO_DSN = 1000

DEFAULT_CLEARANCE_MM   = 0.2
DEFAULT_TRACK_WIDTH_MM = 0.25
DEFAULT_VIA_OD_MM      = 0.6
DEFAULT_VIA_DRILL_MM   = 0.3


def _um(mm: float) -> int:
    """Convert mm to DSN units. With (resolution um 10): 1mm = 10000 units."""
    return int(round(mm * MM_TO_DSN))


def _pad_centre(el: SVGElement) -> tuple[float, float]:
    """Extract (cx, cy) centre from any pad element shape."""
    if el.tag in ('circle', 'ellipse'):
        return float(el.attrs.get('cx', 0)), float(el.attrs.get('cy', 0))
    elif el.tag == 'rect':
        x = float(el.attrs.get('x', 0))
        y = float(el.attrs.get('y', 0))
        w = float(el.attrs.get('width',  0))
        h = float(el.attrs.get('height', 0))
        return round(x + w / 2, 4), round(y + h / 2, 4)
    return 0.0, 0.0


def _pad_size_mm(el: SVGElement) -> float:
    """
    Extract pad size as radius (mm) for a circular padstack.
    Uses max(w, h) / 2 to cover the full pad area.
    """
    if el.tag == 'circle':
        return float(el.attrs.get('r', 0.25))
    elif el.tag == 'ellipse':
        rx = float(el.attrs.get('rx', 0.25))
        ry = float(el.attrs.get('ry', 0.25))
        return max(rx, ry)
    elif el.tag == 'rect':
        w = float(el.attrs.get('width',  0.5))
        h = float(el.attrs.get('height', 0.5))
        return max(w, h) / 2
    return 0.25


def _is_pad(el: SVGElement) -> bool:
    """True if this element is a pad."""
    return (
        el.tag in ('circle', 'rect', 'ellipse')
        and el.attrs.get('data-ref') is not None
        and el.attrs.get('data-pin') is not None
    )


def board_to_dsn(board: BoardSVG,
                 clearance_mm:   float = DEFAULT_CLEARANCE_MM,
                 track_width_mm: float = DEFAULT_TRACK_WIDTH_MM,
                 via_od_mm:      float = DEFAULT_VIA_OD_MM,
                 via_drill_mm:   float = DEFAULT_VIA_DRILL_MM) -> str:

    board_h_units = _um(board.board_h)

    def _flip_y(y: int) -> int:
        return board_h_units - y

    # ── Collect pads ──────────────────────────────────────────────────────────
    pads = []
    nets = set()

    for el in board.pads_g.children:
        if not _is_pad(el):
            continue

        ref   = el.attrs.get('data-ref',   '')
        pin   = el.attrs.get('data-pin',   '')
        net   = el.attrs.get('data-net',   '')
        smd   = el.attrs.get('data-smd',   'false') == 'true'
        layer = el.attrs.get('data-layer', 'F.Cu')

        cx, cy = _pad_centre(el)
        r_mm   = _pad_size_mm(el)
        r_dsn  = _um(r_mm)

        pads.append({
            'ref':   ref,
            'pin':   pin,
            'net':   net,
            'x':     _um(cx),
            'y':     _flip_y(_um(cy)),
            'smd':   smd,
            'layer': layer,
            'r':     r_dsn,
        })
        if net:
            nets.add(net)

    # ── Collect keepouts ──────────────────────────────────────────────────────
    keepouts = []

    for el in board.overlay_g.children:
        if el.attrs.get('data-keepout') != '1':
            continue

        layer       = el.attrs.get('data-layer', 'F.Cu')
        corners_dsn = []

        if el.tag == 'polygon':
            for pair in el.attrs.get('points', '').strip().split():
                try:
                    x_str, y_str = pair.split(',')
                    corners_dsn.append((
                        _um(float(x_str)),
                        _flip_y(_um(float(y_str))),
                    ))
                except ValueError:
                    pass

        elif el.tag == 'rect':
            try:
                x = float(el.attrs.get('x', 0))
                y = float(el.attrs.get('y', 0))
                w = float(el.attrs.get('width',  0))
                h = float(el.attrs.get('height', 0))
            except ValueError:
                continue
            if w <= 0 or h <= 0:
                continue
            for cx, cy in [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]:
                corners_dsn.append((_um(cx), _flip_y(_um(cy))))

        if len(corners_dsn) >= 3:
            keepouts.append({'layer': layer, 'corners': corners_dsn})

    # ── Copper layers — always F.Cu + B.Cu minimum ───────────────────────────
    extra: set[str] = {p['layer'] for p in pads} | {k['layer'] for k in keepouts}
    _INNER = ["In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu"]
    copper_layers = ["F.Cu"] + [l for l in _INNER if l in extra] + ["B.Cu"]

    # ── Group pads by component ref ───────────────────────────────────────────
    components: dict[str, list] = {}
    for p in pads:
        components.setdefault(p['ref'], []).append(p)

    comp_origin: dict[str, tuple] = {}
    for ref, ref_pads in components.items():
        ox = sum(p['x'] for p in ref_pads) // len(ref_pads)
        oy = sum(p['y'] for p in ref_pads) // len(ref_pads)
        comp_origin[ref] = (ox, oy)

    # ── Group pads by net ─────────────────────────────────────────────────────
    net_pins: dict[str, list] = {n: [] for n in nets}
    for p in pads:
        if p['net']:
            net_pins[p['net']].append(p)

    bw = _um(board.board_w)
    bh = board_h_units

    # ── Padstack name helpers ─────────────────────────────────────────────────
    def _smd_ps(layer: str, r: int) -> str:
        return f"smd_{layer.replace('.', '_')}_{r}"

    def _tht_ps(r: int) -> str:
        return f"tht_{r}"

    _via_ps = f"Via[0-1]_{_um(via_od_mm)}:{_um(via_drill_mm)}_um"

    smd_padstacks: set[tuple[str, int]] = {
        (p['layer'], p['r']) for p in pads if p['smd']
    }
    tht_padstacks: set[int] = {
        p['r'] for p in pads if not p['smd']
    }

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append('(pcb board.dsn')
    lines.append('  (parser')
    lines.append('    (string_quote ")')
    lines.append('    (space_in_quoted_tokens on)')
    lines.append('    (host_cad "svg_server")')
    lines.append('    (host_version "1.0")')
    lines.append('  )')
    lines.append('  (resolution um 10)')
    lines.append('  (unit um)')

    # ── Structure ─────────────────────────────────────────────────────────────
    lines.append('  (structure')
    for i, layer_name in enumerate(copper_layers):
        lines.append(f'    (layer "{layer_name}"')
        lines.append(f'      (type signal)')
        lines.append(f'      (property (index {i}))')
        lines.append(f'    )')

    lines.append(f'    (boundary')
    lines.append(f'      (path pcb 0  0 0  0 {bh}  {bw} {bh}  {bw} 0  0 0)')
    lines.append(f'    )')

    for k in keepouts:
        pts    = k['corners']
        pt_str = '  '.join(f'{x} {y}' for x, y in pts)
        first  = f'{pts[0][0]} {pts[0][1]}'
        lines.append(
            f'    (keepout "" (polygon "{k["layer"]}" 0  {pt_str}  {first}))'
        )

    lines.append(f'    (via "{_via_ps}")')
    lines.append(f'    (rule')
    lines.append(f'      (width {_um(track_width_mm)})')
    lines.append(f'      (clearance {_um(clearance_mm)})')
    lines.append(f'      (clearance {_um(clearance_mm)} (type smd_smd))')
    lines.append(f'    )')
    lines.append('  )')

    # ── Library ───────────────────────────────────────────────────────────────
    lines.append('  (library')

    for layer, r in sorted(smd_padstacks):
        name = _smd_ps(layer, r)
        lines.append(f'  (padstack "{name}"')
        lines.append(f'    (shape (circle "{layer}" {r}))')
        lines.append(f'    (attach on)')
        lines.append(f'  )')

    for r in sorted(tht_padstacks):
        name = _tht_ps(r)
        lines.append(f'  (padstack "{name}"')
        for layer_name in copper_layers:
            lines.append(f'    (shape (circle "{layer_name}" {r}))')
        lines.append(f'    (attach off)')
        lines.append(f'  )')

    via_r = _um(via_od_mm / 2)
    lines.append(f'  (padstack "{_via_ps}"')
    for layer_name in copper_layers:
        lines.append(f'    (shape (circle "{layer_name}" {via_r}))')
    lines.append(f'    (attach off)')
    lines.append(f'  )')

    for ref, ref_pads in components.items():
        safe_ref = ref.replace('"', '')
        ox, oy   = comp_origin[ref]
        lines.append(f'  (image "{safe_ref}"')
        for p in ref_pads:
            rx = p['x'] - ox
            ry = p['y'] - oy
            ps = _smd_ps(p['layer'], p['r']) if p['smd'] else _tht_ps(p['r'])
            lines.append(f'    (pin "{ps}" "{p["pin"]}" {rx} {ry})')
        lines.append('  )')

    lines.append('  )')  # end library

    # ── Placement ─────────────────────────────────────────────────────────────
    lines.append('  (placement')
    for ref in components:
        safe_ref = ref.replace('"', '')
        ox, oy   = comp_origin[ref]
        lines.append(f'  (component "{safe_ref}"')
        lines.append(f'    (place "{safe_ref}" {ox} {oy} front 0)')
        lines.append(f'  )')
    lines.append('  )')

    # ── Network ───────────────────────────────────────────────────────────────
    lines.append('  (network')
    for net_name, net_pads in net_pins.items():
        safe_net = net_name.replace('"', '')
        lines.append(f'  (net "{safe_net}"')
        lines.append(f'    (pins')
        for p in net_pads:
            safe_ref = p['ref'].replace('"', '')
            lines.append(f'      "{safe_ref}"-"{p["pin"]}"')
        lines.append(f'    )')
        lines.append(f'  )')

    lines.append('  (class "default"')
    lines.append(f'    (circuit (use_via "{_via_ps}"))')
    lines.append(f'    (rule (width {_um(track_width_mm)}) (clearance {_um(clearance_mm)}))')
    for net_name in nets:
        lines.append(f'    "{net_name.replace(chr(34), "")}"')
    lines.append('  )')
    lines.append('  )')  # end network

    lines.append('  (wiring)')
    lines.append(')')

    return '\n'.join(lines)


def board_to_dsn_file(board: BoardSVG, path: str, **kwargs):
    dsn = board_to_dsn(board, **kwargs)
    with open(path, 'w') as f:
        f.write(dsn)
    return dsn