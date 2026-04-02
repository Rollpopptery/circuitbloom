#!/usr/bin/env python3
"""
kicad_bridge.py — Bridge between route_server and KiCad pcbnew API

Provides:
  - grab_grid(): Extract F.Cu/B.Cu copper into numpy grid
  - draw_track(): Add a track to KiCad board
  - draw_via(): Add a via to KiCad board
  - get_pad_centres(): Get routing endpoints

Usage:
    # Inside KiCad scripting console or with pcbnew installed
    from kicad_bridge import KiCadBridge

    bridge = KiCadBridge("design.kicad_pcb", pitch_mm=0.1)
    grid, pads = bridge.grab_grid()
    # ... route on grid ...
    bridge.draw_track(net="VCC", points=[(10,20), (15,20), (15,25)], layer="F.Cu", width=0.25)
    bridge.draw_via(net="VCC", x=15, y=25, drill=0.3, size=0.6)
    bridge.save()
"""

try:
    import pcbnew
    PCBNEW_AVAILABLE = True
except ImportError:
    PCBNEW_AVAILABLE = False
    print("Warning: pcbnew not available. KiCad bridge will not work.")

import numpy as np
from pathlib import Path


class KiCadBridge:
    """Bridge between route_server grid and KiCad pcbnew."""

    def __init__(self, board_path=None, pitch_mm=0.1):
        """
        Initialize bridge.

        Args:
            board_path: Path to .kicad_pcb file, or None to use current board in KiCad
            pitch_mm: Grid resolution in mm (default 0.1mm)
        """
        if not PCBNEW_AVAILABLE:
            raise RuntimeError("pcbnew module not available. Run inside KiCad or install KiCad Python bindings.")

        self.pitch_mm = pitch_mm
        self.board_path = board_path

        if board_path:
            self.board = pcbnew.LoadBoard(str(board_path))
        else:
            self.board = pcbnew.GetBoard()

        # Calculate board bounds and grid dimensions
        bbox = self.board.GetBoardEdgesBoundingBox()
        self.origin_x = pcbnew.ToMM(bbox.GetX())
        self.origin_y = pcbnew.ToMM(bbox.GetY())
        self.board_width_mm = pcbnew.ToMM(bbox.GetWidth())
        self.board_height_mm = pcbnew.ToMM(bbox.GetHeight())

        self.grid_width = int(self.board_width_mm / pitch_mm) + 1
        self.grid_height = int(self.board_height_mm / pitch_mm) + 1

        # Layer IDs
        self.layer_ids = {
            'F.Cu': pcbnew.F_Cu,
            'B.Cu': pcbnew.B_Cu,
        }

    def mm_to_grid(self, x_mm, y_mm):
        """Convert mm coordinates to grid coordinates."""
        gx = int((x_mm - self.origin_x) / self.pitch_mm)
        gy = int((y_mm - self.origin_y) / self.pitch_mm)
        return gx, gy

    def grid_to_mm(self, gx, gy):
        """Convert grid coordinates to mm coordinates."""
        x_mm = gx * self.pitch_mm + self.origin_x
        y_mm = gy * self.pitch_mm + self.origin_y
        return x_mm, y_mm

    def grab_grid(self):
        """
        Extract copper layers into numpy grids.

        Returns:
            grid: dict with 'F.Cu' and 'B.Cu' numpy arrays
                  Values: 0=empty, >0=net_code, -1=obstacle
            pads: list of pad info dicts for routing endpoints
        """
        grid = {
            'F.Cu': np.zeros((self.grid_height, self.grid_width), dtype=np.int32),
            'B.Cu': np.zeros((self.grid_height, self.grid_width), dtype=np.int32),
        }

        pads = []

        # Rasterize pads
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                self._rasterize_pad(grid, pads, pad)

        # Rasterize existing tracks
        for track in self.board.GetTracks():
            if isinstance(track, pcbnew.PCB_VIA):
                self._rasterize_via(grid, track)
            else:
                self._rasterize_track(grid, track)

        return grid, pads

    def _rasterize_pad(self, grid, pads, pad):
        """Rasterize a pad onto the grid and record its centre."""
        pos = pad.GetPosition()
        x_mm = pcbnew.ToMM(pos.x)
        y_mm = pcbnew.ToMM(pos.y)
        gx, gy = self.mm_to_grid(x_mm, y_mm)

        size = pad.GetSize()
        w_mm = pcbnew.ToMM(size.x)
        h_mm = pcbnew.ToMM(size.y)
        hw = int(w_mm / self.pitch_mm / 2) + 1
        hh = int(h_mm / self.pitch_mm / 2) + 1

        net_code = pad.GetNetCode()
        net_name = pad.GetNetname()

        # Determine layers
        layers = []
        if pad.IsOnLayer(pcbnew.F_Cu):
            layers.append('F.Cu')
        if pad.IsOnLayer(pcbnew.B_Cu):
            layers.append('B.Cu')

        # Through-hole pads are on both layers
        if pad.GetDrillSize().x > 0:
            layers = ['F.Cu', 'B.Cu']

        # Record pad centre for routing
        pads.append({
            'x': x_mm,
            'y': y_mm,
            'gx': gx,
            'gy': gy,
            'net_code': net_code,
            'net': net_name,
            'layers': layers,
            'size': (w_mm, h_mm),
            'ref': pad.GetParentFootprint().GetReference(),
            'pad_num': pad.GetNumber(),
        })

        # Rasterize to grid
        for layer in layers:
            self._fill_rect(grid[layer], gx, gy, hw, hh, net_code)

    def _rasterize_track(self, grid, track):
        """Rasterize a track segment onto the grid."""
        start = track.GetStart()
        end = track.GetEnd()

        x1_mm = pcbnew.ToMM(start.x)
        y1_mm = pcbnew.ToMM(start.y)
        x2_mm = pcbnew.ToMM(end.x)
        y2_mm = pcbnew.ToMM(end.y)

        gx1, gy1 = self.mm_to_grid(x1_mm, y1_mm)
        gx2, gy2 = self.mm_to_grid(x2_mm, y2_mm)

        width_mm = pcbnew.ToMM(track.GetWidth())
        half_width = int(width_mm / self.pitch_mm / 2) + 1

        net_code = track.GetNetCode()
        layer_id = track.GetLayer()

        layer_name = None
        if layer_id == pcbnew.F_Cu:
            layer_name = 'F.Cu'
        elif layer_id == pcbnew.B_Cu:
            layer_name = 'B.Cu'

        if layer_name:
            self._fill_line(grid[layer_name], gx1, gy1, gx2, gy2, half_width, net_code)

    def _rasterize_via(self, grid, via):
        """Rasterize a via onto both layers."""
        pos = via.GetPosition()
        x_mm = pcbnew.ToMM(pos.x)
        y_mm = pcbnew.ToMM(pos.y)
        gx, gy = self.mm_to_grid(x_mm, y_mm)

        size_mm = pcbnew.ToMM(via.GetWidth())
        radius = int(size_mm / self.pitch_mm / 2) + 1

        net_code = via.GetNetCode()

        # Vias go through both layers
        for layer in ['F.Cu', 'B.Cu']:
            self._fill_circle(grid[layer], gx, gy, radius, net_code)

    def _fill_rect(self, grid, cx, cy, hw, hh, value):
        """Fill rectangular area on grid."""
        h, w = grid.shape
        for dy in range(-hh, hh + 1):
            for dx in range(-hw, hw + 1):
                gx, gy = cx + dx, cy + dy
                if 0 <= gx < w and 0 <= gy < h:
                    grid[gy, gx] = value

    def _fill_circle(self, grid, cx, cy, radius, value):
        """Fill circular area on grid."""
        h, w = grid.shape
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx*dx + dy*dy <= radius*radius:
                    gx, gy = cx + dx, cy + dy
                    if 0 <= gx < w and 0 <= gy < h:
                        grid[gy, gx] = value

    def _fill_line(self, grid, x1, y1, x2, y2, half_width, value):
        """Fill line with width on grid using Bresenham."""
        h, w = grid.shape

        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        x, y = x1, y1
        while True:
            # Fill width perpendicular to line
            for wd in range(-half_width, half_width + 1):
                for hd in range(-half_width, half_width + 1):
                    gx, gy = x + wd, y + hd
                    if 0 <= gx < w and 0 <= gy < h:
                        grid[gy, gx] = value

            if x == x2 and y == y2:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

    def draw_track(self, net, points, layer='F.Cu', width=0.25):
        """
        Draw a track (series of segments) on the KiCad board.

        Args:
            net: Net name (string)
            points: List of (x_mm, y_mm) points
            layer: 'F.Cu' or 'B.Cu'
            width: Track width in mm

        Returns:
            List of created track objects
        """
        if len(points) < 2:
            return []

        net_info = self.board.GetNetInfo()
        net_item = net_info.GetNetItem(net)
        if not net_item:
            print(f"Warning: Net '{net}' not found")
            return []

        net_code = net_item.GetNetCode()
        layer_id = self.layer_ids.get(layer, pcbnew.F_Cu)
        width_nm = pcbnew.FromMM(width)

        tracks = []
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]

            track = pcbnew.PCB_TRACK(self.board)
            track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
            track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
            track.SetWidth(width_nm)
            track.SetLayer(layer_id)
            track.SetNetCode(net_code)

            self.board.Add(track)
            tracks.append(track)

        return tracks

    def draw_via(self, net, x, y, drill=0.3, size=0.6):
        """
        Place a via on the KiCad board.

        Args:
            net: Net name (string)
            x, y: Position in mm
            drill: Drill diameter in mm
            size: Via outer diameter in mm

        Returns:
            Created via object
        """
        net_info = self.board.GetNetInfo()
        net_item = net_info.GetNetItem(net)
        if not net_item:
            print(f"Warning: Net '{net}' not found")
            return None

        via = pcbnew.PCB_VIA(self.board)
        via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        via.SetDrill(pcbnew.FromMM(drill))
        via.SetWidth(pcbnew.FromMM(size))
        via.SetNetCode(net_item.GetNetCode())
        via.SetViaType(pcbnew.VIATYPE_THROUGH)

        self.board.Add(via)
        return via

    def move_footprint(self, ref, x, y, rotation=None):
        """
        Move a footprint to a new position.

        Args:
            ref: Reference designator (e.g., 'U1')
            x, y: New position in mm
            rotation: Optional rotation in degrees
        """
        for fp in self.board.GetFootprints():
            if fp.GetReference() == ref:
                fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
                if rotation is not None:
                    fp.SetOrientationDegrees(rotation)
                return True
        print(f"Warning: Footprint '{ref}' not found")
        return False

    def delete_tracks_for_net(self, net):
        """
        Delete all tracks and vias for a given net.

        Args:
            net: Net name (string)

        Returns:
            Number of items deleted
        """
        net_info = self.board.GetNetInfo()
        net_item = net_info.GetNetItem(net)
        if not net_item:
            print(f"Warning: Net '{net}' not found")
            return 0

        net_code = net_item.GetNetCode()

        to_delete = []
        for track in self.board.GetTracks():
            if track.GetNetCode() == net_code:
                to_delete.append(track)

        for item in to_delete:
            self.board.Remove(item)

        return len(to_delete)

    def refresh(self):
        """Refresh KiCad display (only works when running inside KiCad)."""
        try:
            pcbnew.Refresh()
        except:
            pass  # Not running inside KiCad GUI

    def save(self, path=None):
        """
        Save the board to file.

        Args:
            path: Output path, or None to overwrite original
        """
        save_path = path or self.board_path
        if save_path:
            pcbnew.SaveBoard(str(save_path), self.board)
            print(f"Saved: {save_path}")
        else:
            print("Warning: No path specified and no original path known")

    def get_stats(self):
        """Get board statistics."""
        track_count = 0
        via_count = 0
        for track in self.board.GetTracks():
            if isinstance(track, pcbnew.PCB_VIA):
                via_count += 1
            else:
                track_count += 1

        pad_count = sum(len(list(fp.Pads())) for fp in self.board.GetFootprints())
        net_count = self.board.GetNetInfo().GetNetCount()

        return {
            'board_size': (self.board_width_mm, self.board_height_mm),
            'grid_size': (self.grid_width, self.grid_height),
            'pitch_mm': self.pitch_mm,
            'footprints': len(list(self.board.GetFootprints())),
            'pads': pad_count,
            'tracks': track_count,
            'vias': via_count,
            'nets': net_count,
        }


# Standalone test
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python kicad_bridge.py <board.kicad_pcb>")
        sys.exit(1)

    if not PCBNEW_AVAILABLE:
        print("Error: pcbnew module not available")
        sys.exit(1)

    bridge = KiCadBridge(sys.argv[1])
    print("Stats:", bridge.get_stats())

    grid, pads = bridge.grab_grid()
    print(f"Grid F.Cu: {grid['F.Cu'].shape}, non-zero: {np.count_nonzero(grid['F.Cu'])}")
    print(f"Grid B.Cu: {grid['B.Cu'].shape}, non-zero: {np.count_nonzero(grid['B.Cu'])}")
    print(f"Pads: {len(pads)}")
