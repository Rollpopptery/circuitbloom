#!/usr/bin/env python3
"""
pcb_check.py — Quick checks on .kicad_pcb files without opening KiCad.

Usage:
    from pcb_check import is_two_layer, get_layer_count

    if is_two_layer(path):
        # index it
"""

import re

def get_copper_layers(path):
    copper = []
    in_layers = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '(layers' in line:
                    in_layers = True
                    continue
                if in_layers:
                    stripped = line.strip()
                    if stripped == ')':
                        break
                    # Match layer name ending in .Cu specifically
                    import re
                    m = re.search(r'\b(\w+\.Cu)\b', stripped)
                    if m:
                        copper.append(m.group(1))
    except Exception:
        pass
    return copper


def get_layer_count(path):
    return len(get_copper_layers(path))


def is_two_layer(path):
    return get_layer_count(path) == 2


def get_layer_count(path):
    return len(get_copper_layers(path))


def is_two_layer(path):
    return get_layer_count(path) == 2


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        layers = get_copper_layers(path)
        print(f"{path}: {len(layers)} copper layers {layers}")