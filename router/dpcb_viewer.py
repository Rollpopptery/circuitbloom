#!/usr/bin/env python3
"""
DPCB Viewer — tkinter-based viewer for .dpcb board files.
Usage: python dpcb_viewer.py [path/to/board.dpcb]
"""

import sys
import math
import re
import tkinter as tk
from tkinter import filedialog, ttk
from dataclasses import dataclass, field
from typing import Optional

__version__ = "0.3.0"


# ============ DATA STRUCTURES ============

@dataclass
class Rules:
    clearance: float = 0.2
    track: float = 0.25
    via_od: float = 0.6
    via_id: float = 0.3

@dataclass
class Pad:
    num: int
    dx: float
    dy: float

@dataclass
class AbsPad:
    num: int
    x: float
    y: float

@dataclass
class Footprint:
    ref: str
    lib: str
    footprint: str
    x: float
    y: float
    rotation: int = 0
    abs_pads: list = field(default_factory=list)

@dataclass
class Net:
    name: str
    pads: list = field(default_factory=list)  # list of (ref, pin)

@dataclass
class Track:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: str
    net: str

@dataclass
class Via:
    x: float
    y: float
    od: float
    id_: float
    net: str

@dataclass
class Board:
    width: float = 100
    height: float = 80
    layers: int = 2
    rules: Rules = field(default_factory=Rules)
    footprints: list = field(default_factory=list)
    pad_defs: dict = field(default_factory=dict)
    nets: list = field(default_factory=list)
    tracks: list = field(default_factory=list)
    vias: list = field(default_factory=list)


# ============ PARSER ============

def rotate_pad(dx, dy, angle_deg):
    a = math.radians(angle_deg)
    cos_a = math.cos(a)
    sin_a = math.sin(a)
    return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

def parse_dpcb(text):
    b = Board()
    lines = text.split('\n')
    # Strip comments and whitespace
    lines = [re.sub(r'#.*$', '', l).strip() for l in lines]
    lines = [l for l in lines if l]

    for line in lines:
        if line.startswith('BOARD:'):
            m = re.match(r'BOARD:([\d.]+)x([\d.]+)', line)
            if m:
                b.width = float(m.group(1))
                b.height = float(m.group(2))

        elif line.startswith('LAYERS:'):
            b.layers = int(line.split(':')[1])

        elif line.startswith('RULES:'):
            parts = line[6:].split(':')
            for p in parts:
                k, v = p.split('=')
                if k == 'clearance':
                    b.rules.clearance = float(v)
                elif k == 'track':
                    b.rules.track = float(v)
                elif k == 'via':
                    od, id_ = v.split('/')
                    b.rules.via_od = float(od)
                    b.rules.via_id = float(id_)

        elif line.startswith('FP:'):
            m = re.match(r'^FP:([^:]+):([^:]+):([^@]+)@\(([^,]+),([^)]+)\)(?::r(\d+))?', line)
            if m:
                b.footprints.append(Footprint(
                    ref=m.group(1),
                    lib=m.group(2),
                    footprint=m.group(3),
                    x=float(m.group(4)),
                    y=float(m.group(5)),
                    rotation=int(m.group(6)) if m.group(6) else 0
                ))

        elif line.startswith('PADS:'):
            m = re.match(r'^PADS:([^:]+):([^:]+):(.+)$', line)
            if m:
                key = m.group(2)
                pad_str = m.group(3)
                pads = []
                for pm in re.finditer(r'(\d+)@\(([^,]+),([^)]+)\)', pad_str):
                    pads.append(Pad(
                        num=int(pm.group(1)),
                        dx=float(pm.group(2)),
                        dy=float(pm.group(3))
                    ))
                b.pad_defs[key] = pads

        elif line.startswith('NET:'):
            m = re.match(r'^NET:([^:]+):(.+)$', line)
            if m:
                pad_refs = []
                for p in m.group(2).split(','):
                    p = p.strip()
                    ref, pin = p.rsplit('.', 1)
                    pad_refs.append((ref, int(pin)))
                b.nets.append(Net(name=m.group(1), pads=pad_refs))

        elif line.startswith('TRK:'):
            m = re.match(r'^TRK:\(([^,]+),([^)]+)\)->\(([^,]+),([^)]+)\):([^:]+):([^:]+):(.+)$', line)
            if m:
                b.tracks.append(Track(
                    x1=float(m.group(1)), y1=float(m.group(2)),
                    x2=float(m.group(3)), y2=float(m.group(4)),
                    width=float(m.group(5)),
                    layer=m.group(6),
                    net=m.group(7)
                ))

        elif line.startswith('VIA:'):
            m = re.match(r'^VIA:\(([^,]+),([^)]+)\):([^/]+)/([^:]+):(.+)$', line)
            if m:
                b.vias.append(Via(
                    x=float(m.group(1)), y=float(m.group(2)),
                    od=float(m.group(3)), id_=float(m.group(4)),
                    net=m.group(5)
                ))

    # Compute absolute pad positions
    for fp in b.footprints:
        pad_def = b.pad_defs.get(fp.footprint, [])
        fp.abs_pads = []
        for pad in pad_def:
            rx, ry = rotate_pad(pad.dx, pad.dy, fp.rotation)
            fp.abs_pads.append(AbsPad(num=pad.num, x=fp.x + rx, y=fp.y + ry))

    return b


# ============ LAYER COLORS ============

COLOR_FCU = '#ff8844'
COLOR_BCU = '#4488ff'
COLOR_VIA = '#88dd44'
COLOR_PAD = '#d4aa00'
COLOR_OUTLINE = '#444444'

# ============ NET COLORS ============

NET_PALETTE = [
    '#00e5a0', '#ff6b35', '#4488ff', '#ff4488', '#88ff44',
    '#ffaa00', '#aa44ff', '#44ffdd', '#ff4444', '#44aaff',
    '#ddff44', '#ff44aa', '#00ccff', '#ff8844', '#88aaff',
    '#ffdd44', '#cc44ff', '#44ffaa', '#ff6688', '#66ddff'
]

def net_color(name):
    if name == 'GND':
        return '#3366cc'
    if name == 'VCC':
        return '#ff3333'
    h = 0
    for c in name:
        h = ((h << 5) - h + ord(c)) & 0xFFFFFFFF
    return NET_PALETTE[h % len(NET_PALETTE)]


# ============ FOOTPRINT OUTLINES ============

def fp_dimensions(name):
    if 'SOIC-16' in name:
        return 3.9, 9.9
    if 'TSSOP-8' in name:
        return 4.4, 3.0
    if 'R_0805' in name or 'C_0805' in name:
        return 2.0, 1.25
    if 'PinHeader_1x06' in name:
        return 2.54, 15.24
    return 2.0, 2.0


# ============ VIEWER ============

class DPCBViewer:
    def __init__(self, root):
        self.root = root
        self.root.title(f"DPCB Viewer v{__version__}")
        self.root.configure(bg='#0a0e14')
        self.root.geometry("1200x800")

        self.board: Optional[Board] = None
        self.show_fcu = True
        self.show_bcu = True
        self.show_pads = True
        self.show_refs = True
        self.show_ratsnest = False

        # View transform
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None

        self._build_ui()

    def _build_ui(self):
        # Top toolbar
        toolbar = tk.Frame(self.root, bg='#111822', height=40)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="DPCB Viewer", font=("Courier", 12, "bold"),
                 bg='#111822', fg='#e8edf3').pack(side=tk.LEFT, padx=10)

        self.file_label = tk.Label(toolbar, text="No file loaded", font=("Courier", 10),
                                   bg='#111822', fg='#5c6a7a')
        self.file_label.pack(side=tk.LEFT, padx=10)

        # Buttons - right side
        btn_style = dict(font=("Courier", 9), bg='#1e2a3a', fg='#c5cdd8',
                         activebackground='#2a3a4e', activeforeground='#e8edf3',
                         bd=0, padx=8, pady=3, relief=tk.FLAT)

        tk.Button(toolbar, text="Open", command=self.open_file,
                  font=("Courier", 9, "bold"), bg='#00e5a0', fg='#0a0e14',
                  activebackground='#00cc88', bd=0, padx=12, pady=3,
                  relief=tk.FLAT).pack(side=tk.RIGHT, padx=4, pady=6)

        tk.Button(toolbar, text="Reset View", command=self.reset_view,
                  **btn_style).pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_ratsnest = tk.Button(toolbar, text="Ratsnest", command=self.toggle_ratsnest, **btn_style)
        self.btn_ratsnest.pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_refs = tk.Button(toolbar, text="Refs", command=self.toggle_refs,
                                  font=("Courier", 9), bg='#2a3a4e', fg='#e8edf3',
                                  activebackground='#2a3a4e', bd=0, padx=8, pady=3, relief=tk.FLAT)
        self.btn_refs.pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_pads = tk.Button(toolbar, text="Pads", command=self.toggle_pads,
                                  font=("Courier", 9), bg='#2a3a4e', fg='#d4aa00',
                                  activebackground='#2a3a4e', bd=0, padx=8, pady=3, relief=tk.FLAT)
        self.btn_pads.pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_bcu = tk.Button(toolbar, text="B.Cu", command=self.toggle_bcu,
                                 font=("Courier", 9), bg='#2a3a4e', fg='#3366cc',
                                 activebackground='#2a3a4e', bd=0, padx=8, pady=3, relief=tk.FLAT)
        self.btn_bcu.pack(side=tk.RIGHT, padx=2, pady=6)

        self.btn_fcu = tk.Button(toolbar, text="F.Cu", command=self.toggle_fcu,
                                 font=("Courier", 9), bg='#2a3a4e', fg='#cc3333',
                                 activebackground='#2a3a4e', bd=0, padx=8, pady=3, relief=tk.FLAT)
        self.btn_fcu.pack(side=tk.RIGHT, padx=2, pady=6)

        # Main area
        main = tk.Frame(self.root, bg='#0a0e14')
        main.pack(fill=tk.BOTH, expand=True)

        # Canvas
        self.canvas = tk.Canvas(main, bg='#060a10', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Sidebar
        sidebar = tk.Frame(main, bg='#111822', width=240)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar.pack_propagate(False)

        # Info section
        info_frame = tk.Frame(sidebar, bg='#111822')
        info_frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(info_frame, text="BOARD INFO", font=("Courier", 8, "bold"),
                 bg='#111822', fg='#5c6a7a').pack(anchor=tk.W)
        self.info_label = tk.Label(info_frame, text="—", font=("Courier", 9),
                                   bg='#111822', fg='#c5cdd8', justify=tk.LEFT, anchor=tk.W)
        self.info_label.pack(fill=tk.X, pady=(2, 0))

        # Footprints list
        fp_frame = tk.Frame(sidebar, bg='#111822')
        fp_frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(fp_frame, text="FOOTPRINTS", font=("Courier", 8, "bold"),
                 bg='#111822', fg='#5c6a7a').pack(anchor=tk.W)
        self.fp_listbox = tk.Listbox(fp_frame, bg='#0a0e14', fg='#c5cdd8',
                                      font=("Courier", 9), selectbackground='#1e2a3a',
                                      selectforeground='#e8edf3', bd=0, height=8,
                                      highlightthickness=0)
        self.fp_listbox.pack(fill=tk.X, pady=(2, 0))

        # Nets list
        net_frame = tk.Frame(sidebar, bg='#111822')
        net_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 8))
        tk.Label(net_frame, text="NETS", font=("Courier", 8, "bold"),
                 bg='#111822', fg='#5c6a7a').pack(anchor=tk.W)
        self.net_listbox = tk.Listbox(net_frame, bg='#0a0e14', fg='#c5cdd8',
                                       font=("Courier", 9), selectbackground='#1e2a3a',
                                       selectforeground='#e8edf3', bd=0,
                                       highlightthickness=0)
        self.net_listbox.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # Status bar
        self.status = tk.Label(self.root, text="Ready", font=("Courier", 9),
                               bg='#111822', fg='#5c6a7a', anchor=tk.W, padx=10)
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

        # Bindings
        self.canvas.bind('<MouseWheel>', self.on_scroll)         # Windows/Mac
        self.canvas.bind('<Button-4>', self.on_scroll_up)        # Linux
        self.canvas.bind('<Button-5>', self.on_scroll_down)      # Linux
        self.canvas.bind('<ButtonPress-1>', self.on_drag_start)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_drag_end)
        self.canvas.bind('<Motion>', self.on_mouse_move)
        self.canvas.bind('<Configure>', lambda e: self.render())

    # ============ FILE LOADING ============

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open DPCB file",
            filetypes=[("DPCB files", "*.dpcb"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            with open(path, 'r') as f:
                text = f.read()
            self.board = parse_dpcb(text)
            self.board_path = path
            self.reset_view()
            self.update_sidebar()
            short = path.split('/')[-1].split('\\')[-1]
            self.file_label.config(text=short)
            self.status.config(text=f"Loaded: {short} — {len(self.board.footprints)} footprints, "
                                    f"{len(self.board.nets)} nets, {len(self.board.tracks)} tracks, "
                                    f"{len(self.board.vias)} vias")
            self.render()
        except Exception as e:
            self.status.config(text=f"Error: {e}")

    def update_sidebar(self):
        b = self.board
        if not b:
            return

        self.info_label.config(text=f"{b.width}×{b.height}mm\n{b.layers} layers\n"
                                     f"Track: {b.rules.track}mm\nClearance: {b.rules.clearance}mm")

        self.fp_listbox.delete(0, tk.END)
        for fp in b.footprints:
            self.fp_listbox.insert(tk.END, f"{fp.ref}  {fp.footprint}")

        self.net_listbox.delete(0, tk.END)
        for net in b.nets:
            trk_count = sum(1 for t in b.tracks if t.net == net.name)
            status = f"{trk_count} trk" if trk_count > 0 else "unrouted"
            self.net_listbox.insert(tk.END, f"{net.name}  ({status})")

    # ============ VIEW CONTROLS ============

    def reset_view(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.render()

    def toggle_fcu(self):
        self.show_fcu = not self.show_fcu
        self.btn_fcu.config(fg='#cc3333' if self.show_fcu else '#333333')
        self.render()

    def toggle_bcu(self):
        self.show_bcu = not self.show_bcu
        self.btn_bcu.config(fg='#3366cc' if self.show_bcu else '#333333')
        self.render()

    def toggle_pads(self):
        self.show_pads = not self.show_pads
        self.btn_pads.config(fg='#d4aa00' if self.show_pads else '#333333')
        self.render()

    def toggle_refs(self):
        self.show_refs = not self.show_refs
        self.btn_refs.config(fg='#e8edf3' if self.show_refs else '#333333')
        self.render()

    def toggle_ratsnest(self):
        self.show_ratsnest = not self.show_ratsnest
        self.btn_ratsnest.config(fg='#e8edf3' if self.show_ratsnest else '#333333')
        self.render()

    # ============ MOUSE EVENTS ============

    def on_scroll(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        self._zoom_at(event.x, event.y, factor)

    def on_scroll_up(self, event):
        self._zoom_at(event.x, event.y, 1.1)

    def on_scroll_down(self, event):
        self._zoom_at(event.x, event.y, 0.9)

    def _zoom_at(self, mx, my, factor):
        old_zoom = self.zoom
        self.zoom = max(0.1, min(50, self.zoom * factor))
        # Adjust pan so zoom centres on mouse
        ratio = self.zoom / old_zoom
        self.pan_x = mx - ratio * (mx - self.pan_x)
        self.pan_y = my - ratio * (my - self.pan_y)
        self.render()

    def on_drag_start(self, event):
        self.drag_start = (event.x - self.pan_x, event.y - self.pan_y)

    def on_drag(self, event):
        if self.drag_start:
            self.pan_x = event.x - self.drag_start[0]
            self.pan_y = event.y - self.drag_start[1]
            self.render()

    def on_drag_end(self, event):
        self.drag_start = None

    def on_mouse_move(self, event):
        if not self.board:
            return
        bx, by = self.screen_to_board(event.x, event.y)
        if 0 <= bx <= self.board.width and 0 <= by <= self.board.height:
            self.status.config(text=f"({bx:.2f}, {by:.2f}) mm   Zoom: {self.zoom:.1f}x")

    # ============ COORDINATE TRANSFORM ============

    def get_transform(self):
        """Returns (scale, offset_x, offset_y) for board-mm to screen-px."""
        if not self.board:
            return 1, 0, 0
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        margin = 40
        sx = (cw - margin * 2) / self.board.width
        sy = (ch - margin * 2) / self.board.height
        base = min(sx, sy)
        scale = base * self.zoom
        bw = self.board.width * scale
        bh = self.board.height * scale
        ox = (cw - bw) / 2 + self.pan_x
        oy = (ch - bh) / 2 + self.pan_y
        return scale, ox, oy

    def board_to_screen(self, bx, by):
        s, ox, oy = self.get_transform()
        return ox + bx * s, oy + by * s

    def screen_to_board(self, sx, sy):
        s, ox, oy = self.get_transform()
        if s == 0:
            return 0, 0
        return (sx - ox) / s, (sy - oy) / s

    # ============ RENDERING ============

    def render(self):
        c = self.canvas
        c.delete('all')

        if not self.board:
            c.create_text(c.winfo_width() // 2, c.winfo_height() // 2,
                         text="Open a .dpcb file to view", fill='#5c6a7a',
                         font=("Courier", 14))
            return

        b = self.board
        s, ox, oy = self.get_transform()

        def tx(mm_x):
            return ox + mm_x * s

        def ty(mm_y):
            return oy + mm_y * s

        def mm(v):
            return v * s

        # Board outline
        c.create_rectangle(tx(0), ty(0), tx(b.width), ty(b.height),
                          fill='#0f3d1a', outline='#1a6b2a', width=2)

        # Grid at high zoom
        if s > 3:
            step = 1 if s > 8 else 5 if s > 4 else 10
            for x in range(0, int(b.width) + 1, step):
                c.create_line(tx(x), ty(0), tx(x), ty(b.height), fill='#1a5528', width=1)
            for y in range(0, int(b.height) + 1, step):
                c.create_line(tx(0), ty(y), tx(b.width), ty(y), fill='#1a5528', width=1)

        # Keepout overlay
        api = getattr(self, 'api', None)
        grid = api.grid if api else None
        if grid and s > 2:
            import numpy as np
            pitch = 0.1  # GRID_PITCH
            for layer in (0, 1):
                ys, xs = np.where(grid.occupy[layer] == -1)
                if len(xs) > 20000:
                    continue  # too many to render
                for gx, gy in zip(xs, ys):
                    mx, my = gx * pitch, gy * pitch
                    c.create_rectangle(tx(mx), ty(my),
                                       tx(mx + pitch), ty(my + pitch),
                                       fill='#2a1a0a', outline='')

        # Footprint outlines
        for fp in b.footprints:
            w, h = fp_dimensions(fp.footprint)
            if fp.rotation in (90, 270):
                w, h = h, w
            x1 = tx(fp.x - w / 2)
            y1 = ty(fp.y - h / 2)
            x2 = tx(fp.x + w / 2)
            y2 = ty(fp.y + h / 2)
            c.create_rectangle(x1, y1, x2, y2, outline=COLOR_OUTLINE, width=1)

        # Tracks - B.Cu first
        if self.show_bcu:
            for trk in b.tracks:
                if trk.layer != 'B.Cu':
                    continue
                w = max(1, mm(trk.width))
                c.create_line(tx(trk.x1), ty(trk.y1), tx(trk.x2), ty(trk.y2),
                             fill=COLOR_BCU, width=w, capstyle=tk.ROUND)

        # Tracks - F.Cu
        if self.show_fcu:
            for trk in b.tracks:
                if trk.layer != 'F.Cu':
                    continue
                w = max(1, mm(trk.width))
                c.create_line(tx(trk.x1), ty(trk.y1), tx(trk.x2), ty(trk.y2),
                             fill=COLOR_FCU, width=w, capstyle=tk.ROUND)

        # Vias
        for via in b.vias:
            cx, cy = tx(via.x), ty(via.y)
            r = max(2, mm(via.od / 2))
            ri = max(1, mm(via.id_ / 2))
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill='#333333', outline=COLOR_VIA, width=1)
            c.create_oval(cx - ri, cy - ri, cx + ri, cy + ri, fill='#060a10', outline='')

        # Pads
        if self.show_pads:
            for fp in b.footprints:
                for pad in fp.abs_pads:
                    cx, cy = tx(pad.x), ty(pad.y)
                    r = max(2, mm(0.3))
                    c.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=COLOR_PAD, outline='')
                    # Pad numbers at high zoom
                    if s > 6:
                        c.create_text(cx, cy, text=str(pad.num),
                                     fill='#0a0e14', font=("Courier", max(7, int(mm(0.25)))))

        # Reference designators
        if self.show_refs:
            font_size = max(8, int(mm(1.2)))
            for fp in b.footprints:
                c.create_text(tx(fp.x), ty(fp.y), text=fp.ref,
                             fill='#cccccc', font=("Courier", font_size))

        # Ratsnest
        if self.show_ratsnest:
            self._draw_ratsnest(tx, ty)

    def _draw_ratsnest(self, tx, ty):
        if not self.board:
            return
        b = self.board
        fp_map = {fp.ref: fp for fp in b.footprints}

        for net in b.nets:
            # Skip nets that have tracks
            if any(t.net == net.name for t in b.tracks):
                continue
            positions = []
            for ref, pin in net.pads:
                fp = fp_map.get(ref)
                if not fp:
                    continue
                for p in fp.abs_pads:
                    if p.num == pin:
                        positions.append((p.x, p.y))
                        break
            if len(positions) < 2:
                continue
            col = net_color(net.name)
            for i in range(1, len(positions)):
                self.canvas.create_line(
                    tx(positions[i - 1][0]), ty(positions[i - 1][1]),
                    tx(positions[i][0]), ty(positions[i][1]),
                    fill=col, width=1, dash=(2, 4))


# ============ MAIN ============

def main():
    root = tk.Tk()
    viewer = DPCBViewer(root)

    # Load file from command line argument
    if len(sys.argv) > 1:
        viewer.load_file(sys.argv[1])

    # Start API server
    try:
        from dpcb_api import ApiServer
        port = 9876
        # Check for --port argument
        for i, arg in enumerate(sys.argv):
            if arg == '--port' and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        api = ApiServer(viewer, host='0.0.0.0', port=port)
        viewer.api = api
        api.start()
        print(f"API server on 0.0.0.0:{port}")
    except ImportError:
        print("dpcb_api.py not found — running viewer only (no API server)")
    except Exception as e:
        print(f"API server failed to start: {e}")

    root.mainloop()

if __name__ == '__main__':
    main()