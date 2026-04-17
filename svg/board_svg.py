#!/usr/bin/env python3
"""
board_svg.py — SVG object model for a KiCad PCB board.

The BoardSVG class holds the board as a tree of SVGElement objects.
It is the canonical server-side representation — manipulate it directly,
then call to_svg() to serialise to a string for an LLM or browser.

No HTTP, no KiCad dependency.
"""

from __future__ import annotations
from typing import Optional
import xml.sax.saxutils as saxutils


# ── SVG element ───────────────────────────────────────────────────────────────

class SVGElement:
    """A single SVG element node."""

    def __init__(self, tag: str, attrs: dict = None, children: list = None):
        self.tag      = tag
        self.attrs    = attrs or {}
        self.children = children or []

    def to_svg(self, indent: int = 0) -> str:
        pad = "  " * indent
        attr_str = ""
        for k, v in self.attrs.items():
            attr_str += f' {k}="{saxutils.escape(str(v))}"'
        if not self.children:
            return f"{pad}<{self.tag}{attr_str}/>"
        inner = "\n".join(c.to_svg(indent + 1) for c in self.children)
        return f"{pad}<{self.tag}{attr_str}>\n{inner}\n{pad}</{self.tag}>"

    def find_by_id(self, element_id: str) -> Optional["SVGElement"]:
        if self.attrs.get("id") == element_id:
            return self
        for child in self.children:
            found = child.find_by_id(element_id)
            if found:
                return found
        return None

    def find_by_attr(self, key: str, value: str) -> list["SVGElement"]:
        results = []
        if self.attrs.get(key) == value:
            results.append(self)
        for child in self.children:
            results.extend(child.find_by_attr(key, value))
        return results

    def remove_child_by_id(self, element_id: str) -> bool:
        for i, child in enumerate(self.children):
            if child.attrs.get("id") == element_id:
                self.children.pop(i)
                return True
            if child.remove_child_by_id(element_id):
                return True
        return False

    def __repr__(self):
        return f"<SVGElement {self.tag} id={self.attrs.get('id', '-')}>"


# ── BoardSVG ──────────────────────────────────────────────────────────────────

class BoardSVG:
    """
    SVG object model for a PCB board.

    Structure:
        root  <svg viewBox="0 0 w h">
          <style/>
          <rect/>                        board outline
          <g id="layer-F_Cu">            one per copper layer
            <line .../>  ...
          <g id="vias">
            <circle .../>  ...
          <g id="pads">
            <circle|rect|ellipse .../>   correct pad shape
            <text .../>                  pad label
          <g id="overlays">

    All coordinates are in mm. viewBox is in mm.

    Origin:
        origin_x, origin_y — the KiCad absolute coordinates of the
        board's top-left corner (mm). Used when pushing routes back to
        KiCad: kicad_abs = svg_coord + origin.

    Pad element attributes (common to all shapes):
        id          "pad-{ref}-{pin}"
        fill        colour (by layer and SMD/THT)
        data-ref    component reference
        data-pin    pin number/name
        data-net    net name
        data-smd    "true" / "false"
        data-layer  copper layer name (e.g. "F.Cu")
        data-shape  original KiCad shape name

    Shape-specific attributes:
        circle:   cx, cy, r
        rect:     x, y, width, height  (+ transform="rotate(...)" if angled)
        ellipse:  cx, cy, rx, ry       (+ transform="rotate(...)" if angled)
        roundrect: x, y, width, height, rx  (+ transform if angled)
    """

    def __init__(self):
        self.root:      SVGElement = None
        self.layers:    dict[str, SVGElement] = {}
        self.pads_g:    SVGElement = None
        self.vias_g:    SVGElement = None
        self.overlay_g: SVGElement = None
        self.board_w:   float = 0
        self.board_h:   float = 0
        self.origin_x:  float = 0
        self.origin_y:  float = 0

    # ── Build ─────────────────────────────────────────────────────────────────

    @classmethod
    def from_capture(cls, data: dict) -> "BoardSVG":
        self = cls()

        bounds   = data["bounds"]
        origin_x = bounds["min_x"]
        origin_y = bounds["min_y"]
        self.board_w  = round(bounds["max_x"] - origin_x, 4)
        self.board_h  = round(bounds["max_y"] - origin_y, 4)
        self.origin_x = round(origin_x, 4)
        self.origin_y = round(origin_y, 4)

        pads          = data["pads"]
        tracks        = data["tracks"]
        vias          = data["vias"]
        copper_layers = data.get("copper_layers", ["F.Cu", "B.Cu"])

        # ── root <svg> ────────────────────────────────────────────────────────
        self.root = SVGElement("svg", {
            "xmlns":   "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {self.board_w} {self.board_h}",
            "style":   "background:#1a1a1a",
        })

        # ── <style> ───────────────────────────────────────────────────────────
        style = SVGElement("style")
        style.attrs["_text"] = (
            ".pad-label{font-family:monospace;font-size:0.4px;"
            "fill:#ffffff;text-anchor:middle;dominant-baseline:central;"
            "pointer-events:none}"
            ".labels-hidden .pad-label{display:none}"
        )
        self.root.children.append(style)

        # ── board outline ─────────────────────────────────────────────────────
        self.root.children.append(SVGElement("rect", {
            "id":           "board-outline",
            "x":            "0", "y": "0",
            "width":        str(self.board_w),
            "height":       str(self.board_h),
            "fill":         "none",
            "stroke":       "#444444",
            "stroke-width": "0.1",
        }))

        # ── track layers ──────────────────────────────────────────────────────
        by_layer: dict[str, list] = {name: [] for name in copper_layers}
        for t in tracks:
            layer = t.get("layer", "")
            if layer not in by_layer:
                by_layer[layer] = []
            by_layer[layer].append(t)

        for layer_name in copper_layers:
            layer_tracks = by_layer.get(layer_name, [])
            safe_id = layer_name.replace(".", "_").replace(" ", "_")
            g = SVGElement("g", {
                "id":         f"layer-{safe_id}",
                "data-layer": layer_name,
            })
            colour = _layer_colour(layer_name)
            for t in layer_tracks:
                x1 = _snap(t["x1"], origin_x)
                y1 = _snap(t["y1"], origin_y)
                x2 = _snap(t["x2"], origin_x)
                y2 = _snap(t["y2"], origin_y)
                w  = t.get("width", 0.1)
                g.children.append(SVGElement("line", {
                    "x1":             str(x1),
                    "y1":             str(y1),
                    "x2":             str(x2),
                    "y2":             str(y2),
                    "stroke":         colour,
                    "stroke-width":   str(w),
                    "stroke-linecap": "round",
                    "data-net":       t.get("net", ""),
                }))
            self.layers[layer_name] = g
            self.root.children.append(g)

        # ── vias ──────────────────────────────────────────────────────────────
        self.vias_g = SVGElement("g", {"id": "vias"})
        for i, v in enumerate(vias):
            cx  = _snap(v["x"], origin_x)
            cy  = _snap(v["y"], origin_y)
            r_o = v.get("od", 0.6) / 2
            r_i = v.get("id", 0.3) / 2
            net = v.get("net", "")
            self.vias_g.children.append(SVGElement("circle", {
                "id":       f"via-{i}",
                "cx":       str(cx), "cy": str(cy),
                "r":        str(r_o),
                "fill":     "#aaaaaa",
                "data-net": net,
            }))
            self.vias_g.children.append(SVGElement("circle", {
                "cx":   str(cx), "cy": str(cy),
                "r":    str(r_i),
                "fill": "#1a1a1a",
            }))
        self.root.children.append(self.vias_g)

        # ── pads ──────────────────────────────────────────────────────────────
        self.pads_g = SVGElement("g", {"id": "pads"})
        for p in pads:
            cx    = _snap(p["x"], origin_x)
            cy    = _snap(p["y"], origin_y)
            ref   = p.get("ref",   "")
            pin   = p.get("pin",   "")
            net   = p.get("net",   "")
            smd   = p.get("smd",   False)
            layers_list = p.get("layers", [])
            shape = p.get("shape", "circle")
            w     = p.get("w",     0.5)
            h     = p.get("h",     0.5)
            angle = p.get("angle", 0.0)

            pad_layer = _primary_layer(layers_list, copper_layers, smd)
            fill      = _pad_colour(pad_layer, smd)
            pad_id    = f"pad-{ref}-{pin}".replace(" ", "_")
            label     = f"{ref}:{pin}" if ref else pin

            # Common data attributes
            common = {
                "id":         pad_id,
                "fill":       fill,
                "data-ref":   ref,
                "data-pin":   pin,
                "data-net":   net,
                "data-smd":   "true" if smd else "false",
                "data-layer": pad_layer,
                "data-shape": shape,
            }

            pad_el = _make_pad_element(shape, cx, cy, w, h, angle, common)
            self.pads_g.children.append(pad_el)

            if label:
                self.pads_g.children.append(SVGElement("text", {
                    "class": "pad-label",
                    "x":     str(cx),
                    "y":     str(cy),
                    "_text": label,
                }))

        self.root.children.append(self.pads_g)

        # ── overlays ──────────────────────────────────────────────────────────
        self.overlay_g = SVGElement("g", {"id": "overlays"})
        self.root.children.append(self.overlay_g)

        return self

    # ── Serialise ─────────────────────────────────────────────────────────────

    def to_svg(self) -> str:
        return _element_to_svg(self.root, indent=0)

    def to_svg_compact(self) -> str:
        return _element_to_svg_compact(self.root)

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_element(self, element_id: str) -> Optional[SVGElement]:
        return self.root.find_by_id(element_id)

    def get_by_net(self, net: str) -> list[SVGElement]:
        return self.root.find_by_attr("data-net", net)

    def get_by_ref(self, ref: str) -> list[SVGElement]:
        return self.root.find_by_attr("data-ref", ref)

    # ── Mutate ────────────────────────────────────────────────────────────────

    def add_element(self, tag: str, attrs: dict,
                    parent_id: str = "overlays") -> SVGElement:
        parent = self.root.find_by_id(parent_id) if parent_id else self.overlay_g
        if parent is None:
            parent = self.overlay_g
        el = SVGElement(tag, attrs)
        parent.children.append(el)
        return el

    def update_element(self, element_id: str, attrs: dict) -> bool:
        el = self.root.find_by_id(element_id)
        if el is None:
            return False
        el.attrs.update(attrs)
        return True

    def remove_element(self, element_id: str) -> bool:
        return self.root.remove_child_by_id(element_id)

    def clear_overlays(self):
        self.overlay_g.children.clear()

    # ── Route extraction (for push to KiCad) ─────────────────────────────────

    def extract_routed_tracks(self) -> list[dict]:
        """Extract routed polylines from overlays as track segment dicts."""
        tracks = []
        for el in self.overlay_g.children:
            if el.tag != 'polyline':
                continue
            if el.attrs.get('data-via') == '1':
                continue
            net   = el.attrs.get('data-net',   '')
            layer = el.attrs.get('data-layer', 'F.Cu')
            try:
                width = float(el.attrs.get('stroke-width', 0.25))
            except ValueError:
                width = 0.25

            points_str = el.attrs.get('points', '')
            pts = []
            for pair in points_str.strip().split():
                try:
                    x_str, y_str = pair.split(',')
                    pts.append((float(x_str), float(y_str)))
                except ValueError:
                    pass

            for i in range(len(pts) - 1):
                tracks.append({
                    'x1':    pts[i][0],
                    'y1':    pts[i][1],
                    'x2':    pts[i + 1][0],
                    'y2':    pts[i + 1][1],
                    'width': width,
                    'layer': layer,
                    'net':   net,
                })
        return tracks

    def extract_routed_vias(self) -> list[dict]:
        """Extract routed via circles from overlays as via dicts."""
        vias = []
        for el in self.overlay_g.children:
            if el.tag != 'circle':
                continue
            if el.attrs.get('data-via') != '1':
                continue
            try:
                cx = float(el.attrs.get('cx', 0))
                cy = float(el.attrs.get('cy', 0))
                r  = float(el.attrs.get('r',  0.3))
            except ValueError:
                continue

            try:
                od    = float(el.attrs.get('data-od',    r * 2))
                drill = float(el.attrs.get('data-drill', r))
            except ValueError:
                od    = r * 2
                drill = r

            vias.append({
                'x':   cx,
                'y':   cy,
                'od':  od,
                'id':  drill,
                'net': el.attrs.get('data-net', ''),
            })
        return vias


# ── Pad shape factory ─────────────────────────────────────────────────────────

def _make_pad_element(shape: str, cx: float, cy: float,
                      w: float, h: float, angle: float,
                      common: dict) -> SVGElement:
    """
    Create the correct SVG element for a pad shape.

    Args:
        shape:  'circle', 'rect', 'oval', 'roundrect', or 'other'
        cx, cy: pad centre in SVG coords (mm)
        w, h:   pad width and height in mm
        angle:  rotation in degrees
        common: dict of data-* and other shared attributes

    Returns:
        SVGElement
    """
    # Rotation transform centred on the pad
    transform = f"rotate({angle},{cx},{cy})" if angle else None

    if shape == 'circle':
        r = round(w / 2, 4)
        attrs = {**common, "cx": str(cx), "cy": str(cy), "r": str(r)}
        return SVGElement("circle", attrs)

    elif shape == 'rect':
        attrs = {
            **common,
            "x":      str(round(cx - w / 2, 4)),
            "y":      str(round(cy - h / 2, 4)),
            "width":  str(round(w, 4)),
            "height": str(round(h, 4)),
        }
        if transform:
            attrs["transform"] = transform
        return SVGElement("rect", attrs)

    elif shape == 'oval':
        attrs = {
            **common,
            "cx": str(cx),
            "cy": str(cy),
            "rx": str(round(w / 2, 4)),
            "ry": str(round(h / 2, 4)),
        }
        if transform:
            attrs["transform"] = transform
        return SVGElement("ellipse", attrs)

    elif shape == 'roundrect':
        # Corner radius ~25% of the smaller dimension (KiCad default)
        rx = round(min(w, h) * 0.25, 4)
        attrs = {
            **common,
            "x":      str(round(cx - w / 2, 4)),
            "y":      str(round(cy - h / 2, 4)),
            "width":  str(round(w, 4)),
            "height": str(round(h, 4)),
            "rx":     str(rx),
        }
        if transform:
            attrs["transform"] = transform
        return SVGElement("rect", attrs)

    else:
        # 'other' / unknown — fall back to circle using largest dimension
        r = round(max(w, h) / 2, 4)
        attrs = {**common, "cx": str(cx), "cy": str(cy), "r": str(r)}
        return SVGElement("circle", attrs)


# ── Helpers ───────────────────────────────────────────────────────────────────

LAYER_COLOURS = {
    "F.Cu":   "#c83232",
    "B.Cu":   "#3264c8",
    "In1.Cu": "#c8a000",
    "In2.Cu": "#00a0c8",
    "In3.Cu": "#a000c8",
    "In4.Cu": "#00c8a0",
}

_PAD_SMD_COLOURS = {
    "F.Cu":   "#e05050",
    "B.Cu":   "#5080e0",
    "In1.Cu": "#c8a000",
    "In2.Cu": "#00a0c8",
}
_PAD_THT_COLOUR = "#d4a000"


def _layer_colour(layer: str) -> str:
    return LAYER_COLOURS.get(layer, "#888888")


def _pad_colour(layer: str, smd: bool) -> str:
    if smd:
        return _PAD_SMD_COLOURS.get(layer, "#d4a000")
    return _PAD_THT_COLOUR


def _primary_layer(layers: list, copper_layers: list[str], smd: bool) -> str:
    if not smd:
        return "F.Cu"
    _ID_TO_NAME = {
        3:  "F.Cu",
        34: "B.Cu",
        4:  "In1.Cu",
        5:  "In2.Cu",
        6:  "In3.Cu",
        7:  "In4.Cu",
    }
    for lid in layers:
        name = _ID_TO_NAME.get(lid)
        if name and name in copper_layers:
            return name
    return copper_layers[0] if copper_layers else "F.Cu"


def _snap(v: float, origin: float) -> float:
    return round(v - origin, 4)


def _element_to_svg_compact(el: SVGElement) -> str:
    if el.tag == "text" and "pad-label" in el.attrs.get("class", ""):
        return ""
    if el.tag == "style":
        return ""

    text_content = el.attrs.get("_text", None)
    real_attrs   = {k: v for k, v in el.attrs.items() if not k.startswith("_")}

    attr_str = ""
    for k, v in real_attrs.items():
        attr_str += f' {k}="{saxutils.escape(str(v))}"'

    if not el.children and text_content is None:
        return f"<{el.tag}{attr_str}/>"
    if text_content is not None and not el.children:
        return f"<{el.tag}{attr_str}>{saxutils.escape(str(text_content))}</{el.tag}>"

    inner = "".join(_element_to_svg_compact(c) for c in el.children)
    return f"<{el.tag}{attr_str}>{inner}</{el.tag}>"


def _element_to_svg(el: SVGElement, indent: int = 0) -> str:
    pad_str = "  " * indent

    text_content = el.attrs.get("_text", None)
    real_attrs   = {k: v for k, v in el.attrs.items() if not k.startswith("_")}

    attr_str = ""
    for k, v in real_attrs.items():
        attr_str += f' {k}="{saxutils.escape(str(v))}"'

    if el.tag == "style" and text_content:
        return f"{pad_str}<style>{text_content}</style>"
    if not el.children and text_content is None:
        return f"{pad_str}<{el.tag}{attr_str}/>"
    if text_content is not None and not el.children:
        return f"{pad_str}<{el.tag}{attr_str}>{saxutils.escape(str(text_content))}</{el.tag}>"

    inner = "\n".join(_element_to_svg(c, indent + 1) for c in el.children)
    return f"{pad_str}<{el.tag}{attr_str}>\n{inner}\n{pad_str}</{el.tag}>"