#!/usr/bin/env python3
"""
pad_info.py — Pad and component lookup via KiCad IPC.

Provides pad-level and component-level queries for the currently
loaded board. Lazy-loads the ComponentInfo on first use.

Usage:
    from pad_info import get_pad_info, get_component_info

    pad = get_pad_info("U2", "43")
    comp = get_component_info("U2")
"""

from grab_layer import find_socket

_comp_info = None


def _get_comp_info():
    """Lazy-load ComponentInfo from KiCad."""
    global _comp_info
    if _comp_info is not None:
        return _comp_info
    from component_info import ComponentInfo
    sock = find_socket()
    if not sock:
        return None
    _comp_info = ComponentInfo(sock)
    return _comp_info


def reset():
    """Reset cached ComponentInfo (call after opening a new board)."""
    global _comp_info
    _comp_info = None


def get_pad_info(ref, pin):
    """Look up a pad by ref and pin number.

    Returns:
        dict with ref, pin, net, x, y, smd, value, footprint, description
        or {"error": "..."} on failure
    """
    info = _get_comp_info()
    if info is None:
        return {"error": "KiCad IPC not available"}
    result = info.pad(ref, str(pin))
    if result is None:
        return {"error": f"pad {ref}.{pin} not found"}
    return result


def get_component_info(ref):
    """Look up a component by reference designator.

    Returns:
        dict with ref, value, footprint, description, datasheet,
        x, y, rotation, mounting, pins
        or {"error": "..."} on failure
    """
    info = _get_comp_info()
    if info is None:
        return {"error": "KiCad IPC not available"}
    result = info.component(ref)
    if result is None:
        return {"error": f"component {ref} not found"}
    return result
