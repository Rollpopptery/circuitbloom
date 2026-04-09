#!/usr/bin/env python3
"""
component_info.py — Look up component and pin details from a KiCad board.

Usage:
    from component_info import ComponentInfo
    from grab_layer import find_socket

    info = ComponentInfo(find_socket())

    # Look up a component
    info.component("U2")
    # {'ref': 'U2', 'value': 'ATMEGA32U4', 'footprint': 'Package_QFP:TQFP-44_10x10mm_P0.8mm',
    #  'description': '', 'datasheet': '', 'x': 47.1, 'y': 43.5, 'rotation': 0.0,
    #  'mounting': 'smd', 'pins': ['1', '2', ...]}

    # Look up a specific pad
    info.pad("U2", "43")
    # {'ref': 'U2', 'pin': '43', 'net': 'GND', 'x': 41.4, 'y': 40.3, 'smd': True,
    #  'value': 'ATMEGA32U4', 'footprint': 'Package_QFP:TQFP-44_10x10mm_P0.8mm'}

    # List all components
    info.components()

    # Find components by value
    info.find(value="10K")
    info.find(footprint="0603")
"""

from kipy import KiCad


class ComponentInfo:
    def __init__(self, socket_path):
        kicad = KiCad(socket_path=socket_path)
        board = kicad.get_board()
        self._build(board)

    def _build(self, board):
        self._components = {}
        self._pads = {}  # keyed by (ref, pin)

        # Build component info from footprints
        for fp in board.get_footprints():
            ref = fp.reference_field.text.value if fp.reference_field else ""
            if not ref:
                continue

            value = fp.value_field.text.value if fp.value_field else ""
            desc = fp.description_field.text.value if fp.description_field else ""
            datasheet = fp.datasheet_field.text.value if fp.datasheet_field else ""
            defn_id = str(fp.definition.id) if fp.definition else ""
            rotation = fp.orientation.degrees if hasattr(fp.orientation, "degrees") else 0

            mounting = "unknown"
            try:
                ms = fp.attributes.mounting_style
                # kipy enum: 0=through_hole, 1=smd
                if ms == 1 or str(ms).lower().endswith("smd"):
                    mounting = "smd"
                elif ms == 0 or "through" in str(ms).lower():
                    mounting = "through_hole"
            except (AttributeError, TypeError):
                pass

            self._components[ref] = {
                "ref": ref,
                "value": value,
                "footprint": defn_id,
                "description": desc,
                "datasheet": datasheet,
                "x": round(fp.position.x / 1_000_000, 3),
                "y": round(fp.position.y / 1_000_000, 3),
                "rotation": rotation,
                "mounting": mounting,
                "pins": [],
            }

        # Build pad info and attach pin lists to components
        fp_positions = {
            ref: (c["x"], c["y"]) for ref, c in self._components.items()
        }

        for pad in board.get_pads():
            pin = pad.number
            x = round(pad.position.x / 1_000_000, 3)
            y = round(pad.position.y / 1_000_000, 3)
            net = pad.net.name if pad.net else ""

            # Match pad to nearest footprint
            ref = ""
            best_dist = float("inf")
            for fp_ref, (fx, fy) in fp_positions.items():
                d = ((x - fx) ** 2 + (y - fy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    ref = fp_ref

            smd = pad.pad_type == 2  # PT_SMD

            self._pads[(ref, pin)] = {
                "ref": ref,
                "pin": pin,
                "net": net,
                "x": x,
                "y": y,
                "smd": smd,
            }

            if ref in self._components and pin:
                self._components[ref]["pins"].append(pin)

        # Sort pin lists
        for comp in self._components.values():
            comp["pins"] = sorted(set(comp["pins"]), key=_pin_sort_key)

    def component(self, ref):
        """Look up a component by reference designator."""
        return self._components.get(ref)

    def pad(self, ref, pin):
        """Look up a specific pad. Returns pad info merged with component info."""
        p = self._pads.get((ref, str(pin)))
        if not p:
            return None
        result = dict(p)
        comp = self._components.get(ref)
        if comp:
            result["value"] = comp["value"]
            result["footprint"] = comp["footprint"]
            result["description"] = comp["description"]
        return result

    def components(self):
        """List all components (sorted by ref)."""
        return [
            self._components[r]
            for r in sorted(self._components, key=_ref_sort_key)
        ]

    def find(self, value=None, footprint=None):
        """Find components matching value and/or footprint substring."""
        results = []
        for comp in self._components.values():
            if value and value.lower() not in comp["value"].lower():
                continue
            if footprint and footprint.lower() not in comp["footprint"].lower():
                continue
            results.append(comp)
        return sorted(results, key=lambda c: _ref_sort_key(c["ref"]))


def _pin_sort_key(pin):
    """Sort pins numerically when possible."""
    try:
        return (0, int(pin))
    except ValueError:
        return (1, pin)


def _ref_sort_key(ref):
    """Sort refs like C1, C2, C10 correctly."""
    prefix = ref.rstrip("0123456789")
    num = ref[len(prefix):]
    try:
        return (prefix, int(num))
    except ValueError:
        return (prefix, 0)


if __name__ == "__main__":
    from grab_layer import find_socket

    sock = find_socket()
    if not sock:
        print("No KiCad socket found")
        exit(1)

    info = ComponentInfo(sock)

    print("=== Components ===")
    for c in info.components():
        print(f"  {c['ref']:8s}  {c['value']:20s}  {c['footprint']}")

    print("\n=== Sample pad lookup: U2 pin 43 ===")
    p = info.pad("U2", "43")
    if p:
        for k, v in p.items():
            print(f"  {k}: {v}")
