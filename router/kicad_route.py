#!/usr/bin/env python3
"""
kicad_route.py — Push routes (tracks, vias) to KiCad via IPC API.

Usage:
    from kicad_route import push_tracks, push_vias, push_routes

    push_routes(socket_path, tracks, vias, origin_x, origin_y)
"""

from kipy import KiCad
from kipy.board_types import Track, Via
from kipy.geometry import Vector2

# Layer constants
BL_F_CU = 3
BL_B_CU = 34


def get_net_map(board):
    """Build net name -> Net object map."""
    net_map = {}
    for net in board.get_nets():
        if net.name:
            net_map[net.name] = net
    return net_map


def push_tracks(socket_path, tracks, origin_x, origin_y):
    """Push track segments to KiCad board.

    Args:
        socket_path: KiCad IPC socket path
        tracks: List of track dicts with x1, y1, x2, y2, width, layer, net
        origin_x, origin_y: Board origin offset to convert back to absolute coords

    Returns:
        (ok, message) tuple
    """
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()
    net_map = get_net_map(board)

    new_tracks = []
    skipped = 0

    for t in tracks:
        net_name = t.get("net", "")
        if net_name not in net_map:
            skipped += 1
            continue

        track = Track()

        # Convert mm to nanometers and add origin offset
        start = Vector2()
        start.x = int((t["x1"] + origin_x) * 1000000)
        start.y = int((t["y1"] + origin_y) * 1000000)
        track.start = start

        end = Vector2()
        end.x = int((t["x2"] + origin_x) * 1000000)
        end.y = int((t["y2"] + origin_y) * 1000000)
        track.end = end

        track.width = int(t.get("width", 0.25) * 1000000)
        track.layer = BL_F_CU if t.get("layer") == "F.Cu" else BL_B_CU
        track.net = net_map[net_name]

        new_tracks.append(track)

    if not new_tracks:
        return False, f"No valid tracks to push (skipped {skipped})"

    commit = board.begin_commit()
    board.create_items(new_tracks)
    board.push_commit(commit, f"Add {len(new_tracks)} tracks from router")

    msg = f"Pushed {len(new_tracks)} tracks"
    if skipped:
        msg += f" (skipped {skipped})"
    return True, msg


def push_vias(socket_path, vias, origin_x, origin_y):
    """Push vias to KiCad board.

    Args:
        socket_path: KiCad IPC socket path
        vias: List of via dicts with x, y, od, id, net
        origin_x, origin_y: Board origin offset

    Returns:
        (ok, message) tuple
    """
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()
    net_map = get_net_map(board)

    new_vias = []
    skipped = 0

    for v in vias:
        net_name = v.get("net", "")
        if net_name not in net_map:
            skipped += 1
            continue

        via = Via()

        pos = Vector2()
        pos.x = int((v["x"] + origin_x) * 1000000)
        pos.y = int((v["y"] + origin_y) * 1000000)
        via.position = pos

        via.net = net_map[net_name]

        # Set via size (outer diameter) and drill diameter in nm
        od = v.get("od", 0.6)  # default 0.6mm
        drill = v.get("id", 0.3)  # default 0.3mm drill
        via.diameter = int(od * 1000000)
        via.drill_diameter = int(drill * 1000000)

        new_vias.append(via)

    if not new_vias:
        return False, f"No valid vias to push (skipped {skipped})"

    commit = board.begin_commit()
    board.create_items(new_vias)
    board.push_commit(commit, f"Add {len(new_vias)} vias from router")

    msg = f"Pushed {len(new_vias)} vias"
    if skipped:
        msg += f" (skipped {skipped})"
    return True, msg


def delete_tracks(socket_path, net_name=None):
    """Delete tracks from KiCad board.

    Args:
        socket_path: KiCad IPC socket path
        net_name: If provided, only delete tracks on this net. If None, delete all.

    Returns:
        (ok, message) tuple
    """
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()

    tracks_to_remove = []
    for track in board.get_tracks():
        if net_name is None or (track.net and track.net.name == net_name):
            tracks_to_remove.append(track)

    if not tracks_to_remove:
        return False, f"No tracks found" + (f" for net {net_name}" if net_name else "")

    commit = board.begin_commit()
    board.remove_items(tracks_to_remove)
    board.push_commit(commit, f"Remove {len(tracks_to_remove)} tracks")

    return True, f"Deleted {len(tracks_to_remove)} tracks"


def push_routes(socket_path, tracks, vias, origin_x, origin_y):
    """Push both tracks and vias to KiCad in a single commit.

    Args:
        socket_path: KiCad IPC socket path
        tracks: List of track dicts
        vias: List of via dicts
        origin_x, origin_y: Board origin offset

    Returns:
        (ok, message) tuple
    """
    kicad = KiCad(socket_path=socket_path)
    board = kicad.get_board()
    net_map = get_net_map(board)

    items = []
    track_count = 0
    via_count = 0
    skipped = 0

    # Build tracks
    for t in tracks:
        net_name = t.get("net", "")
        if net_name not in net_map:
            skipped += 1
            continue

        track = Track()

        start = Vector2()
        start.x = int((t["x1"] + origin_x) * 1000000)
        start.y = int((t["y1"] + origin_y) * 1000000)
        track.start = start

        end = Vector2()
        end.x = int((t["x2"] + origin_x) * 1000000)
        end.y = int((t["y2"] + origin_y) * 1000000)
        track.end = end

        track.width = int(t.get("width", 0.25) * 1000000)
        track.layer = BL_F_CU if t.get("layer") == "F.Cu" else BL_B_CU
        track.net = net_map[net_name]

        items.append(track)
        track_count += 1

    # Build vias
    for v in vias:
        net_name = v.get("net", "")
        if net_name not in net_map:
            skipped += 1
            continue

        via = Via()

        pos = Vector2()
        pos.x = int((v["x"] + origin_x) * 1000000)
        pos.y = int((v["y"] + origin_y) * 1000000)
        via.position = pos

        via.net = net_map[net_name]

        items.append(via)
        via_count += 1

    if not items:
        return False, f"No valid items to push (skipped {skipped})"

    commit = board.begin_commit()
    board.create_items(items)
    board.push_commit(commit, f"Add {track_count} tracks, {via_count} vias from router")

    msg = f"Pushed {track_count} tracks, {via_count} vias"
    if skipped:
        msg += f" (skipped {skipped})"
    return True, msg
