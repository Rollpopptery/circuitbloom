#!/usr/bin/env python3
"""
Circuit Viewer - Tkinter zoomable viewer for .circuit files
Loads a .circuit JSON file, computes connectivity-based layout,
and renders components as boxes with ratsnest lines.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import json
import math
import numpy as np
from collections import defaultdict


class LayoutEngine:
    """Computes 2D positions from connectivity data."""

    def __init__(self, circuit_data):
        self.circuit = circuit_data
        self.components = circuit_data["components"]
        self.comp_ids = list(self.components.keys())
        self.n = len(self.comp_ids)
        self.idx_map = {cid: i for i, cid in enumerate(self.comp_ids)}

    def extract_nets(self):
        """Extract nets from pin assignments."""
        nets = defaultdict(list)
        for comp_id, comp in self.components.items():
            for pin_num, pin in comp["pins"].items():
                net = pin.get("net")
                if net:
                    nets[net].append(comp_id)
        return nets

    def build_connectivity_matrix(self):
        """Build matrix of shared net counts between components."""
        nets = self.extract_nets()
        matrix = np.zeros((self.n, self.n))

        for net_name, comps in nets.items():
            unique_comps = list(set(comps))
            for i in range(len(unique_comps)):
                for j in range(i + 1, len(unique_comps)):
                    a = self.idx_map[unique_comps[i]]
                    b = self.idx_map[unique_comps[j]]
                    matrix[a][b] += 1
                    matrix[b][a] += 1

        return matrix

    def spectral_layout(self):
        """Use spectral embedding to get 2D positions from connectivity."""
        conn = self.build_connectivity_matrix()

        if self.n < 3:
            # Too few components for spectral, just place in a line
            positions = {}
            for i, cid in enumerate(self.comp_ids):
                positions[cid] = (i * 200, 0)
            return positions

        # Build Laplacian
        degree = np.diag(conn.sum(axis=1))
        laplacian = degree - conn

        # Add small identity to avoid singularity for disconnected components
        laplacian += np.eye(self.n) * 0.01

        # Eigendecomposition
        eigenvalues, eigenvectors = np.linalg.eigh(laplacian)

        # Use 2nd and 3rd smallest eigenvectors for 2D coordinates
        # (1st is the trivial constant vector)
        x_coords = eigenvectors[:, 1]
        y_coords = eigenvectors[:, 2]

        # Scale to reasonable canvas coordinates
        scale = 400

        def normalize(arr):
            r = arr.max() - arr.min()
            if r < 1e-10:
                return np.zeros_like(arr)
            return (arr - arr.min()) / r

        x_coords = normalize(x_coords) * scale
        y_coords = normalize(y_coords) * scale

        positions = {}
        for i, cid in enumerate(self.comp_ids):
            positions[cid] = (x_coords[i] + 100, y_coords[i] + 100)

        return positions

    def get_box_size(self, comp_id):
        """Determine box size based on pin count."""
        pin_count = len(self.components[comp_id]["pins"])
        side_pins = max(1, math.ceil(pin_count / 2))
        width = 80
        height = max(50, side_pins * 25 + 10)
        return width, height

    def resolve_overlaps(self, positions, padding=30, iterations=100):
        """Push apart overlapping boxes while preserving topology."""
        pos = {k: list(v) for k, v in positions.items()}
        sizes = {k: self.get_box_size(k) for k in self.comp_ids}

        for _ in range(iterations):
            moved = False
            for i, a in enumerate(self.comp_ids):
                for b in self.comp_ids[i + 1:]:
                    ax, ay = pos[a]
                    bx, by = pos[b]
                    aw, ah = sizes[a]
                    bw, bh = sizes[b]

                    # Half sizes with padding
                    haw = aw / 2 + padding
                    hah = ah / 2 + padding
                    hbw = bw / 2 + padding
                    hbh = bh / 2 + padding

                    dx = bx - ax
                    dy = by - ay

                    overlap_x = (haw + hbw) - abs(dx)
                    overlap_y = (hah + hbh) - abs(dy)

                    if overlap_x > 0 and overlap_y > 0:
                        # Push apart along the axis of least overlap
                        if overlap_x < overlap_y:
                            push = overlap_x / 2 + 1
                            if dx >= 0:
                                pos[a][0] -= push
                                pos[b][0] += push
                            else:
                                pos[a][0] += push
                                pos[b][0] -= push
                        else:
                            push = overlap_y / 2 + 1
                            if dy >= 0:
                                pos[a][1] -= push
                                pos[b][1] += push
                            else:
                                pos[a][1] += push
                                pos[b][1] -= push
                        moved = True

            if not moved:
                break

        return {k: tuple(v) for k, v in pos.items()}

    def compute_layout(self):
        """Full layout pipeline: spectral placement -> overlap resolution."""
        positions = self.spectral_layout()
        positions = self.resolve_overlaps(positions)
        return positions


class CircuitViewer(tk.Tk):
    """Zoomable, pannable Tkinter viewer for .circuit files."""

    # Color scheme
    BG_COLOR = "#1e1e2e"
    BOX_COLOR = "#313244"
    BOX_OUTLINE = "#89b4fa"
    PIN_COLOR = "#f38ba8"
    PIN_TEXT_COLOR = "#cdd6f4"
    LABEL_COLOR = "#89dceb"
    TYPE_COLOR = "#a6adc8"
    NET_COLORS = [
        "#f38ba8", "#a6e3a1", "#89b4fa", "#fab387",
        "#cba6f7", "#f9e2af", "#94e2d5", "#eba0ac",
        "#74c7ec", "#b4befe", "#f2cdcd", "#a6e3a1",
    ]
    CANVAS_BG = "#1e1e2e"

    def __init__(self):
        super().__init__()
        self.title("Circuit Viewer")
        self.geometry("1200x800")
        self.configure(bg=self.BG_COLOR)

        self.circuit_data = None
        self.positions = None
        self.scale = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self._drag_start = None
        self._drag_comp = None

        self._build_ui()
        self._bind_events()

    def _build_ui(self):
        """Build the UI."""
        # Toolbar
        toolbar = tk.Frame(self, bg="#181825", height=40)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        btn_open = tk.Button(
            toolbar, text="Open .circuit", command=self._open_file,
            bg="#313244", fg="#cdd6f4", activebackground="#45475a",
            activeforeground="#cdd6f4", bd=0, padx=15, pady=5,
            font=("monospace", 10)
        )
        btn_open.pack(side=tk.LEFT, padx=10, pady=5)

        btn_fit = tk.Button(
            toolbar, text="Fit", command=self._fit_view,
            bg="#313244", fg="#cdd6f4", activebackground="#45475a",
            activeforeground="#cdd6f4", bd=0, padx=15, pady=5,
            font=("monospace", 10)
        )
        btn_fit.pack(side=tk.LEFT, padx=5, pady=5)

        btn_save = tk.Button(
            toolbar, text="Save", command=self._save_file,
            bg="#313244", fg="#a6e3a1", activebackground="#45475a",
            activeforeground="#a6e3a1", bd=0, padx=15, pady=5,
            font=("monospace", 10)
        )
        btn_save.pack(side=tk.LEFT, padx=5, pady=5)

        btn_relayout = tk.Button(
            toolbar, text="Re-layout", command=self._relayout,
            bg="#313244", fg="#fab387", activebackground="#45475a",
            activeforeground="#fab387", bd=0, padx=15, pady=5,
            font=("monospace", 10)
        )
        btn_relayout.pack(side=tk.LEFT, padx=5, pady=5)

        self.info_label = tk.Label(
            toolbar, text="No file loaded", bg="#181825", fg="#6c7086",
            font=("monospace", 9)
        )
        self.info_label.pack(side=tk.RIGHT, padx=10)

        # Canvas
        self.canvas = tk.Canvas(
            self, bg=self.CANVAS_BG, highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def _bind_events(self):
        """Bind mouse events for zoom and pan."""
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-4>", self._on_scroll)
        self.canvas.bind("<Button-5>", self._on_scroll)
        # Left click: drag components or pan
        self.canvas.bind("<ButtonPress-1>", self._on_left_down)
        self.canvas.bind("<B1-Motion>", self._on_left_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_up)
        # Middle click: always pan
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.bind("<Configure>", self._on_resize)

    def _screen_to_world(self, sx, sy):
        """Convert screen coordinates to world coordinates."""
        wx = (sx - self.pan_x) / self.scale
        wy = (sy - self.pan_y) / self.scale
        return wx, wy

    def _hit_test(self, sx, sy):
        """Find which component is under screen coords, if any."""
        if not self.positions or not self.circuit_data:
            return None
        wx, wy = self._screen_to_world(sx, sy)
        for comp_id, (cx, cy) in self.positions.items():
            w, h = self.layout_engine.get_box_size(comp_id)
            if (cx - w / 2 <= wx <= cx + w / 2 and
                    cy - h / 2 <= wy <= cy + h / 2):
                return comp_id
        return None

    def _on_left_down(self, event):
        """Left mouse down: check if on a component or empty space."""
        self._drag_comp = self._hit_test(event.x, event.y)
        self._drag_start = (event.x, event.y)
        if self._drag_comp:
            # Highlight selected component
            self._redraw()

    def _on_left_move(self, event):
        if not self._drag_start:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]

        if self._drag_comp:
            # Move component in world coords
            world_dx = dx / self.scale
            world_dy = dy / self.scale
            cx, cy = self.positions[self._drag_comp]
            self.positions[self._drag_comp] = (cx + world_dx, cy + world_dy)
            self._drag_start = (event.x, event.y)
            self._redraw()
        else:
            # Pan canvas
            self.pan_x += dx
            self.pan_y += dy
            self._drag_start = (event.x, event.y)
            self._redraw()

    def _on_left_up(self, event):
        self._drag_comp = None
        self._drag_start = None

    def _on_scroll(self, event):
        """Zoom in/out centered on mouse position."""
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)

        if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
            factor = 1.15
        elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
            factor = 1 / 1.15
        else:
            return

        self.scale *= factor
        self.pan_x = cx - (cx - self.pan_x) * factor
        self.pan_y = cy - (cy - self.pan_y) * factor
        self._redraw()

    def _on_pan_start(self, event):
        self._drag_start = (event.x, event.y)

    def _on_pan_move(self, event):
        if self._drag_start:
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
            self.pan_x += dx
            self.pan_y += dy
            self._drag_start = (event.x, event.y)
            self._redraw()

    def _on_pan_end(self, event):
        self._drag_start = None

    def _on_resize(self, event):
        if self.circuit_data:
            self._redraw()

    def _open_file(self):
        """Open a .bloom or .circuit file."""
        path = filedialog.askopenfilename(
            filetypes=[("Bloom files", "*.bloom"), ("Circuit files", "*.circuit"), ("JSON files", "*.json"), ("All", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, "r") as f:
                self.circuit_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}")
            return

        self.file_path = path
        engine = LayoutEngine(self.circuit_data)
        self.layout_engine = engine

        # Check if positions exist in file
        has_positions = all(
            "position" in comp
            for comp in self.circuit_data["components"].values()
        )

        if has_positions:
            # Load stored positions
            self.positions = {}
            for comp_id, comp in self.circuit_data["components"].items():
                pos = comp["position"]
                self.positions[comp_id] = (pos["x"], pos["y"])
        else:
            # Auto-layout
            self.positions = engine.compute_layout()
            # Store positions back into circuit data
            self._store_positions()

        name = self.circuit_data.get("project", {}).get("name", "Unknown")
        n_comp = len(self.circuit_data["components"])
        nets = engine.extract_nets()
        n_nets = len(nets)
        self.info_label.config(text=f"{name}  |  {n_comp} components  |  {n_nets} nets  |  {path}")

        self._fit_view()

    def _store_positions(self):
        """Store current positions into circuit data."""
        for comp_id, (x, y) in self.positions.items():
            self.circuit_data["components"][comp_id]["position"] = {
                "x": round(x, 2),
                "y": round(y, 2)
            }

    def _save_file(self):
        """Save circuit data with positions back to file."""
        if not self.circuit_data or not hasattr(self, 'file_path'):
            return

        self._store_positions()

        try:
            with open(self.file_path, "w") as f:
                json.dump(self.circuit_data, f, indent=2)
            self.info_label.config(
                text=self.info_label.cget("text").rstrip(" *") + "  [saved]"
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save:\n{e}")

    def _relayout(self):
        """Recalculate layout from connectivity, discarding stored positions."""
        if not self.circuit_data:
            return
        engine = LayoutEngine(self.circuit_data)
        self.layout_engine = engine
        self.positions = engine.compute_layout()
        self._store_positions()
        self._fit_view()

        self._fit_view()

    def _fit_view(self):
        """Fit all components in view."""
        if not self.positions:
            return

        self.update_idletasks()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        if cw < 10 or ch < 10:
            return

        # Find bounding box
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")

        for cid, (x, y) in self.positions.items():
            w, h = self.layout_engine.get_box_size(cid)
            min_x = min(min_x, x - w / 2)
            min_y = min(min_y, y - h / 2)
            max_x = max(max_x, x + w / 2)
            max_y = max(max_y, y + h / 2)

        margin = 60
        data_w = max_x - min_x + margin * 2
        data_h = max_y - min_y + margin * 2

        if data_w < 1 or data_h < 1:
            return

        self.scale = min(cw / data_w, ch / data_h)
        self.pan_x = cw / 2 - (min_x + max_x) / 2 * self.scale
        self.pan_y = ch / 2 - (min_y + max_y) / 2 * self.scale
        self._redraw()

    def _world_to_screen(self, x, y):
        """Convert world coordinates to screen coordinates."""
        return x * self.scale + self.pan_x, y * self.scale + self.pan_y

    def _get_pin_positions(self, comp_id):
        """Calculate screen positions of each pin on a component box."""
        comp = self.circuit_data["components"][comp_id]
        cx, cy = self.positions[comp_id]
        w, h = self.layout_engine.get_box_size(comp_id)

        pins = list(comp["pins"].items())
        pin_count = len(pins)
        left_count = pin_count // 2
        right_count = pin_count - left_count

        pin_positions = {}

        # Left side pins
        for i in range(left_count):
            pin_num = pins[i][0]
            py = cy - h / 2 + (i + 1) * h / (left_count + 1)
            px = cx - w / 2
            pin_positions[pin_num] = (px, py)

        # Right side pins
        for i in range(right_count):
            pin_num = pins[left_count + i][0]
            py = cy - h / 2 + (i + 1) * h / (right_count + 1)
            px = cx + w / 2
            pin_positions[pin_num] = (px, py)

        return pin_positions

    def _redraw(self):
        """Redraw the entire canvas."""
        self.canvas.delete("all")

        if not self.circuit_data or not self.positions:
            return

        components = self.circuit_data["components"]
        nets = self.layout_engine.extract_nets()

        # Assign colors only to connected nets (multiple components), grey for unconnected
        GREY = "#585b70"
        net_color_map = {}
        color_idx = 0
        for net_name, comps in nets.items():
            if len(set(comps)) >= 2:
                net_color_map[net_name] = self.NET_COLORS[color_idx % len(self.NET_COLORS)]
                color_idx += 1
            else:
                net_color_map[net_name] = GREY

        # Draw component boxes
        for comp_id, (cx, cy) in self.positions.items():
            comp = components[comp_id]
            w, h = self.layout_engine.get_box_size(comp_id)

            # Box corners in screen coords
            x1, y1 = self._world_to_screen(cx - w / 2, cy - h / 2)
            x2, y2 = self._world_to_screen(cx + w / 2, cy + h / 2)

            # Draw box - highlight if being dragged
            is_selected = hasattr(self, '_drag_comp') and self._drag_comp == comp_id
            outline = "#f9e2af" if is_selected else self.BOX_OUTLINE
            width = 3 if is_selected else 2
            self.canvas.create_rectangle(
                x1, y1, x2, y2,
                fill=self.BOX_COLOR, outline=outline, width=width
            )

            # Component ID label
            scx, scy = self._world_to_screen(cx, cy - 8)
            label_size = max(8, min(12, int(11 * self.scale)))
            self.canvas.create_text(
                scx, scy, text=comp_id,
                fill=self.LABEL_COLOR, font=("monospace", label_size, "bold")
            )

            # Type label
            scx2, scy2 = self._world_to_screen(cx, cy + 8)
            type_size = max(6, min(9, int(8 * self.scale)))
            type_text = comp.get("type", "")
            self.canvas.create_text(
                scx2, scy2, text=type_text,
                fill=self.TYPE_COLOR, font=("monospace", type_size)
            )

            # Draw pins with net labels
            pin_positions = self._get_pin_positions(comp_id)
            pins = comp["pins"]

            for pin_num, (px, py) in pin_positions.items():
                sx, sy = self._world_to_screen(px, py)
                r = max(2, min(4, int(3 * self.scale)))
                pin_data = pins[pin_num]
                net_name = pin_data.get("net", "")
                net_color = net_color_map.get(net_name, "#585b70")

                # Pin dot colored by net
                self.canvas.create_oval(
                    sx - r, sy - r, sx + r, sy + r,
                    fill=net_color, outline=net_color
                )

                # Pin name inside the box
                pin_label = pin_data.get("name", pin_num)
                pin_font_size = max(5, min(8, int(7 * self.scale)))

                is_left = px < self.positions[comp_id][0]

                if is_left:
                    self.canvas.create_text(
                        sx + r + 3, sy, text=pin_label, anchor=tk.W,
                        fill=self.PIN_TEXT_COLOR, font=("monospace", pin_font_size)
                    )
                else:
                    self.canvas.create_text(
                        sx - r - 3, sy, text=pin_label, anchor=tk.E,
                        fill=self.PIN_TEXT_COLOR, font=("monospace", pin_font_size)
                    )

                # Net label OUTSIDE the box
                if net_name:
                    net_font_size = max(5, min(8, int(7 * self.scale)))
                    stub_len = max(8, int(15 * self.scale))

                    if is_left:
                        # Stub line going left
                        self.canvas.create_line(
                            sx, sy, sx - stub_len, sy,
                            fill=net_color, width=2
                        )
                        self.canvas.create_text(
                            sx - stub_len - 3, sy, text=net_name, anchor=tk.E,
                            fill=net_color, font=("monospace", net_font_size, "bold")
                        )
                    else:
                        # Stub line going right
                        self.canvas.create_line(
                            sx, sy, sx + stub_len, sy,
                            fill=net_color, width=2
                        )
                        self.canvas.create_text(
                            sx + stub_len + 3, sy, text=net_name, anchor=tk.W,
                            fill=net_color, font=("monospace", net_font_size, "bold")
                        )


def main():
    app = CircuitViewer()
    app.mainloop()


if __name__ == "__main__":
    main()