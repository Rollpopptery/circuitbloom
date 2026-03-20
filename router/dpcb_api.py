#!/usr/bin/env python3
"""
dpcb_api.py — TCP command server for LLM-driven PCB routing.

Runs as a thread inside the viewer. Accepts text commands on a TCP socket,
calls the router, updates board state, triggers viewer re-render.

Commands:
    route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto]
    via <net> <x,y>
    unroute <net> [path_index]
    load <filename>
    save <filename>
    status
    nets
    quit

Start with:
    from dpcb_api import ApiServer
    api = ApiServer(viewer, host='127.0.0.1', port=9876)
    api.start()
"""

import os
import socket
import threading
import traceback
from dpcb_router import RouterGrid, route_by_name, route_tap_by_name, GRID_PITCH, _flood_same_net
from dpcb_router8 import route8_by_name
from dpcb_pathset import RouteSet, tracks_to_dpcb_lines, KEEPOUT_NET_ID
from via_check import check_vias, format_viacheck, DEFAULT_THRESHOLD_MM
from crowding_check import check_crowding, format_crowding, DEFAULT_CLEARANCE_THRESHOLD_MM
from pad_crowding_check import check_pad_crowding, format_pad_crowding, DEFAULT_PAD_THRESHOLD_MM
from ratsnest_check import check_ratsnest, format_ratsnest, DEFAULT_RATSNEST_THRESHOLD_MM
from force_check import compute_force, compute_force_all, format_force, format_force_all
from repulsion_check import compute_repulsion, compute_repulsion_all, format_repulsion, format_repulsion_all, DEFAULT_REPULSION_THRESHOLD_MM
from pad_pressure import compute_pressure, compute_pressure_all, format_pressure, format_pressure_all, DEFAULT_PRESSURE_THRESHOLD_MM
from discipline import prompt as discipline_prompt
from dpcb_log import log as design_log


class ApiServer:
    """
    TCP command server that bridges the LLM to the router and viewer.
    
    viewer must provide:
        viewer.board          — current Board object
        viewer.router_grid    — RouterGrid instance (or None, will be created)
        viewer.render()       — trigger canvas redraw (called via root.after for thread safety)
        viewer.load_file(path) — load a .dpcb file
        viewer.root           — tkinter root for thread-safe callbacks
    """

    def __init__(self, viewer, host='127.0.0.1', port=9876):
        self.viewer = viewer
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        self.thread = None
        self.grid = None
        self.routeset = None
        self.keepouts_data = None

    def start(self):
        """Start the API server in a background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the API server."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass

    def _run(self):
        """Main server loop."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.host, self.port))
        except OSError as e:
            print(f"[API] Failed to bind to {self.host}:{self.port}: {e}")
            return

        self.server_socket.listen(1)
        self.server_socket.settimeout(1.0)
        print(f"[API] Listening on {self.host}:{self.port}")

        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                print(f"[API] Connection from {addr}")
                self._handle_connection(conn)
            except socket.timeout:
                continue
            except OSError:
                break

        print("[API] Server stopped")

    def _handle_connection(self, conn):
        """Handle a single client connection. Reads line-by-line."""
        conn.settimeout(None)
        buf = ""
        try:
            while self.running:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data.decode('utf-8', errors='replace')

                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue

                    response = self._handle_command(line)
                    conn.sendall((response + '\n.\n').encode('utf-8'))

                    if line.lower() == 'quit':
                        conn.close()
                        return
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            print(f"[API] Connection error: {e}")
        finally:
            try:
                conn.close()
            except:
                pass
            print("[API] Client disconnected")

    def _handle_command(self, line):
        """Parse and execute a command. Returns response string."""
        parts = line.split()
        if not parts:
            return "ERR: empty command"

        cmd = parts[0].lower()

        try:
            if cmd == 'route':
                return self._cmd_route(parts[1:])
            elif cmd == 'route_tap':
                return self._cmd_route_tap(parts[1:])
            elif cmd == 'unroute':
                return self._cmd_unroute(parts[1:])
            elif cmd == 'load':
                return self._cmd_load(parts[1:])
            elif cmd == 'save':
                return self._cmd_save(parts[1:])
            elif cmd == 'status':
                return self._cmd_status()
            elif cmd == 'nets':
                return self._cmd_nets()
            elif cmd == 'waypoints':
                return self._cmd_waypoints(parts[1:])
            elif cmd == 'pushout':
                return self._cmd_pushout(parts[1:])
            elif cmd == 'pads':
                return self._cmd_pads(parts[1:])
            elif cmd == 'clearkeepouts':
                return self._cmd_clearkeepouts(parts[1:])
            elif cmd == 'keepouts':
                return self._cmd_keepouts(parts[1:])
            elif cmd == 'move':
                return self._cmd_move(parts[1:])
            elif cmd == 'probe':
                return self._cmd_probe(parts[1:])
            elif cmd == 'via':
                return self._cmd_via(parts[1:])
            elif cmd == 'viacheck':
                return self._cmd_viacheck(parts[1:])
            elif cmd == 'check_crowding':
                return self._cmd_check_crowding(parts[1:])
            elif cmd == 'check_crowding_pads':
                return self._cmd_check_crowding_pads(parts[1:])
            elif cmd == 'check_ratsnest':
                return self._cmd_check_ratsnest(parts[1:])
            elif cmd == 'force':
                return self._cmd_force(parts[1:])
            elif cmd == 'repulsion':
                return self._cmd_repulsion(parts[1:])
            elif cmd == 'pressure':
                return self._cmd_pressure(parts[1:])
            elif cmd == 'get_vias':
                return self._cmd_get_vias()
            elif cmd == 'get_transitions':
                return self._cmd_get_transitions(parts[1:])
            elif cmd == 'log_note':
                return self._cmd_log_note(parts[1:])
            elif cmd == 'unroute_seg':
                return self._cmd_unroute_seg(parts[1:])
            elif cmd == 'discipline':
                return self._cmd_discipline()
            elif cmd == 'quit':
                return "OK: goodbye"
            elif cmd == 'help':
                return self._cmd_help()
            else:
                return f"ERR: unknown command '{cmd}'. Try: help"
        except Exception as e:
            traceback.print_exc()
            return f"ERR: {e}"

    # ============ COMMANDS ============

    def _cmd_route(self, args):
        """route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto] [margin=N] [use8]"""
        if len(args) < 3:
            return "ERR: usage: route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto] [margin=N] [use8]"

        net_name = args[0]
        try:
            x1, y1 = [float(v) for v in args[1].split(',')]
            x2, y2 = [float(v) for v in args[2].split(',')]
        except ValueError:
            return "ERR: coordinates must be x,y (e.g. 3.0,20.0)"

        layer_mode = 'auto'
        margin_override = None
        use8 = False
        for a in args[3:]:
            if a in ('F.Cu', 'B.Cu', 'auto'):
                layer_mode = a
            elif a.startswith('margin='):
                try:
                    margin_override = int(a.split('=', 1)[1])
                except ValueError:
                    return "ERR: margin must be an integer (grid cells, e.g. margin=1)"
            elif a == 'use8':
                use8 = True

        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"
        router_fn = route8_by_name if use8 else route_by_name
        result = router_fn(grid, net_name, x1, y1, x2, y2,
                           layer_mode=layer_mode,
                           margin_override=margin_override)

        if result.success:
            net_id = grid.get_net_id(net_name)
            path = result.path

            # Verify path endpoint matches requested destination
            # (start may drift due to same-net flood — that's expected)
            gx2_req, gy2_req = grid.mm_to_grid(x2, y2)
            end_dist = abs(path[-1][0] - gx2_req) + abs(path[-1][1] - gy2_req)
            max_drift = 5  # 0.5mm in grid cells
            if end_dist > max_drift:
                ex, ey = grid.grid_to_mm(path[-1][0], path[-1][1])
                return (f"FAIL: path missed destination — "
                        f"requested ({x2},{y2}) "
                        f"but path ends at ({ex:.1f},{ey:.1f})")

            src_pad = (path[0][0], path[0][1], path[0][2])
            dst_pad = (path[-1][0], path[-1][1], path[-1][2])
            tw = self.viewer.board.rules.track if self.viewer.board else 0.2
            rid, output = routeset.add_route(net_id, src_pad, dst_pad, path, grid,
                                             track_width_mm=tw,
                                             start_mm=(x1, y1), end_mm=(x2, y2))
            self._apply_track_output(net_name, output)
            self._request_render()

            # Include waypoints in response
            wp_str = self._path_waypoints(path, grid)
            resp = f"OK: {result.message}\n  path: {wp_str}"
            use8_str = ' use8' if use8 else ''
            margin_str = f' margin={margin_override}' if margin_override is not None else ''
            self._log(f"route {net_name} {args[1]} {args[2]} {layer_mode}{margin_str}{use8_str} | {result.message}")
            return resp

        use8_str = ' use8' if use8 else ''
        margin_str = f' margin={margin_override}' if margin_override is not None else ''
        self._log(f"route {net_name} {args[1]} {args[2]} {layer_mode}{margin_str}{use8_str} | FAIL: {result.message}")
        return f"FAIL: {result.message}"

    def _cmd_route_tap(self, args):
        """route_tap <net> <x,y> [F.Cu|B.Cu|auto] [margin=N]
        Route from a pad to the nearest existing trace on the same net."""
        if len(args) < 2:
            return "ERR: usage: route_tap <net> <x,y> [F.Cu|B.Cu|auto] [margin=N]"

        net_name = args[0]
        try:
            x1, y1 = [float(v) for v in args[1].split(',')]
        except ValueError:
            return "ERR: coordinates must be x,y"

        layer_mode = 'auto'
        margin_override = None
        for a in args[2:]:
            if a in ('F.Cu', 'B.Cu', 'auto'):
                layer_mode = a
            elif a.startswith('margin='):
                try:
                    margin_override = int(a.split('=', 1)[1])
                except ValueError:
                    return "ERR: margin must be an integer"

        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        result = route_tap_by_name(grid, net_name, x1, y1,
                                   layer_mode=layer_mode,
                                   margin_override=margin_override)

        if result.success:
            net_id = grid.get_net_id(net_name)
            path = result.path
            src_pad = (path[0][0], path[0][1], path[0][2])
            dst_pad = (path[-1][0], path[-1][1], path[-1][2])
            tw = self.viewer.board.rules.track if self.viewer.board else 0.2
            end_mm = result.tap_point
            rid, output = routeset.add_route(net_id, src_pad, dst_pad, path, grid,
                                             track_width_mm=tw,
                                             start_mm=(x1, y1), end_mm=end_mm)
            self._apply_track_output(net_name, output)
            self._request_render()

            wp_str = self._path_waypoints(path, grid)
            tap_str = f"  tapped at ({end_mm[0]:.1f},{end_mm[1]:.1f})" if end_mm else ""
            margin_str = f' margin={margin_override}' if margin_override is not None else ''
            self._log(f"route_tap {net_name} {args[1]} {layer_mode}{margin_str} | {result.message}")
            return f"OK: {result.message}{tap_str}\n  path: {wp_str}"

        margin_str = f' margin={margin_override}' if margin_override is not None else ''
        self._log(f"route_tap {net_name} {args[1]} {layer_mode}{margin_str} | FAIL: {result.message}")
        return f"FAIL: {result.message}"

    def _cmd_unroute(self, args):
        """unroute <net>"""
        if len(args) < 1:
            return "ERR: usage: unroute <net>"

        net_name = args[0]

        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        removed = routeset.remove_by_name(net_name, grid)

        # Remove tracks and vias from board object for viewer
        had_board_tracks = any(t.net == net_name for t in self.viewer.board.tracks)
        had_board_vias = any(v.net == net_name for v in self.viewer.board.vias)
        self._remove_tracks_from_board(net_name, None, grid)

        # Force grid rebuild if vias were removed (via cells not tracked by routeset)
        if had_board_vias:
            self.grid = None
            self.routeset = None

        self._request_render()
        self._log(f"unroute {net_name} | removed {removed} route(s)")
        return f"OK: unrouted {removed} route(s) for {net_name}"

    def _cmd_unroute_seg(self, args):
        """unroute_seg <net> <x1,y1> <x2,y2> [tolerance]
        Remove track segment(s) matching net and endpoints within tolerance (mm).
        Clears grid cells for removed segments. Does not remove vias.
        """
        if len(args) < 3:
            return "ERR: usage: unroute_seg <net> <x1,y1> <x2,y2> [tolerance]"

        net_name = args[0]
        try:
            x1, y1 = [float(v) for v in args[1].split(',')]
            x2, y2 = [float(v) for v in args[2].split(',')]
        except ValueError:
            return "ERR: coordinates must be x,y"

        tol = 0.15
        if len(args) > 3:
            try:
                tol = float(args[3])
            except ValueError:
                pass

        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        grid, routeset = self._ensure_grid()

        def close(a, b):
            return abs(a - b) <= tol

        def match(t):
            if t.net != net_name:
                return False
            fwd = close(t.x1, x1) and close(t.y1, y1) and close(t.x2, x2) and close(t.y2, y2)
            rev = close(t.x1, x2) and close(t.y1, y2) and close(t.x2, x1) and close(t.y2, y1)
            return fwd or rev

        removed = [t for t in board.tracks if match(t)]
        if not removed:
            return f"ERR: no matching segment for {net_name} ({x1},{y1})->({x2},{y2}) tol={tol}"

        # Clear grid cells for removed tracks
        if grid:
            from dpcb_router import LAYER_IDS, _line_cells
            net_id = grid.get_net_id(net_name)
            for t in removed:
                layer = LAYER_IDS.get(t.layer, 0)
                w = max(1, int(round(t.width / GRID_PITCH)))
                gx1, gy1 = grid.mm_to_grid(t.x1, t.y1)
                gx2, gy2 = grid.mm_to_grid(t.x2, t.y2)
                cells = _line_cells(gx1, gy1, gx2, gy2)
                hw = w // 2
                for cx, cy in cells:
                    for dy in range(-hw, hw + 1):
                        for dx in range(-hw, hw + 1):
                            grid.clear_cell(layer, cx + dx, cy + dy, net_id)

        board.tracks = [t for t in board.tracks if not match(t)]

        self._request_render()
        self._log(f"unroute_seg {net_name} ({x1},{y1})->({x2},{y2}) | removed {len(removed)} seg(s)")
        return f"OK: removed {len(removed)} segment(s) from {net_name}"

    def _cmd_load(self, args):
        """load <filename>"""
        if len(args) < 1:
            return "ERR: usage: load <filename>"

        path = ' '.join(args)
        try:
            # Load via viewer (thread-safe)
            self.viewer.root.after(0, lambda: self.viewer.load_file(path))
            # Reset grid and pathset so they get rebuilt on next route
            self.grid = None
            self.routeset = None
            return f"OK: loading {path}"
        except Exception as e:
            return f"ERR: {e}"

    def _cmd_save(self, args):
        """save [filename] — defaults to loaded file if no filename given"""
        if len(args) < 1:
            path = getattr(self.viewer, 'board_path', None)
            if not path:
                return "ERR: no file loaded and no filename given"
        else:
            path = ' '.join(args)
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        try:
            text = self._board_to_dpcb(board)
            with open(path, 'w') as f:
                f.write(text)
            return f"OK: saved to {path}"
        except Exception as e:
            return f"ERR: {e}"

    def _cmd_status(self):
        """Return current board and grid status."""
        board = self.viewer.board
        if not board:
            return "OK: no board loaded"

        grid, _ = self._ensure_grid()
        stats = grid.stats() if grid else {}

        net_count = len(board.nets)
        trk_count = len(board.tracks)
        via_count = len(board.vias)
        fp_count = len(board.footprints)

        return (f"OK: {board.width_mm if hasattr(board, 'width_mm') else board.width}x"
                f"{board.height_mm if hasattr(board, 'height_mm') else board.height}mm "
                f"fps={fp_count} nets={net_count} trks={trk_count} vias={via_count} "
                f"grid={stats.get('grid_size','')} "
                f"fcu={stats.get('pct_fcu','')} bcu={stats.get('pct_bcu','')}")

    def _cmd_nets(self):
        """List all nets with their pad connections and routed status."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        lines = []
        for net in board.nets:
            trk_count = sum(1 for t in board.tracks if t.net == net.name)
            pad_str = ','.join(f"{r}.{p}" for r, p in net.pads)
            status = f"{trk_count}trk" if trk_count > 0 else "unrouted"
            lines.append(f"  {net.name}: {pad_str} [{status}]")

        return "OK: " + str(len(board.nets)) + " nets\n" + '\n'.join(lines)

    def _cmd_waypoints(self, args):
        """waypoints <net> — show route info for a net."""
        if len(args) < 1:
            return "ERR: usage: waypoints <net>"

        net_name = args[0]
        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        net_id = grid.get_net_id(net_name)
        if not net_id:
            return f"ERR: unknown net {net_name}"

        routes = routeset.get_routes_for_net(net_id)
        if not routes:
            return f"ERR: no routes for {net_name}"

        lines = [f"OK: {net_name}: {len(routes)} route(s)"]
        for rid, route in routes:
            sx, sy = grid.grid_to_mm(*route.src_pad[:2])
            dx, dy = grid.grid_to_mm(*route.dst_pad[:2])
            lines.append(f"  route {rid}: ({sx},{sy})->({dx},{dy}) "
                         f"{len(route.keepouts)} keepouts")
        return '\n'.join(lines)

    def _cmd_pushout(self, args):
        """pushout <net> [amount] — push routes away from obstacles using keepout zones."""
        if len(args) < 1:
            return "ERR: usage: pushout <net> [amount]"

        net_name = args[0]
        amount = int(args[1]) if len(args) > 1 else 5

        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        net_id = grid.get_net_id(net_name)
        if not net_id:
            return f"ERR: unknown net {net_name}"

        routes = routeset.get_routes_for_net(net_id)
        if not routes:
            return f"ERR: no routes for {net_name}"

        # Remove existing board tracks for this net before pushout
        self._remove_tracks_from_board(net_name, None, grid)

        tw = self.viewer.board.rules.track if self.viewer.board else 0.2
        results = []
        for rid, route in routes:
            stats, output = routeset.pushout(rid, grid, amount=amount,
                                             track_width_mm=tw)
            if output:
                self._apply_track_output(net_name, output)
            results.append((rid, stats))

        self._request_render()

        # Build response
        parts = []
        for rid, stats in results:
            parts.append(f"route {rid}: {stats.get('message', '?')} "
                         f"(keepouts: {stats.get('total_keepouts', 0)})")
        return f"OK: pushout {net_name}\n  " + "\n  ".join(parts)

    def _cmd_pads(self, args):
        """pads <net> — show pad positions for a net."""
        if len(args) < 1:
            return "ERR: usage: pads <net>"

        net_name = args[0]
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        # Find the net
        net = None
        for n in board.nets:
            if n.name == net_name:
                net = n
                break
        if not net:
            return f"ERR: unknown net {net_name}"

        # Build ref->footprint lookup
        fp_map = {fp.ref: fp for fp in board.footprints}

        lines = [f"OK: {net_name} pads:"]
        for ref, pin in net.pads:
            fp = fp_map.get(ref)
            if not fp:
                lines.append(f"  {ref}.{pin}: footprint not found")
                continue
            for pad in fp.abs_pads:
                if pad.num == pin:
                    lines.append(f"  {ref}.{pin} @ ({pad.x},{pad.y})")
                    break
            else:
                lines.append(f"  {ref}.{pin}: pad not found")
        return '\n'.join(lines)

    def _cmd_clearkeepouts(self, args):
        """clearkeepouts <net> — remove all keepout zones for a net's routes."""
        if len(args) < 1:
            return "ERR: usage: clearkeepouts <net>"

        net_name = args[0]
        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        net_id = grid.get_net_id(net_name)
        if not net_id:
            return f"ERR: unknown net {net_name}"

        routes = routeset.get_routes_for_net(net_id)
        total = 0
        for rid, route in routes:
            total += len(route.keepouts)
            routeset.clear_keepouts(rid)
        return f"OK: cleared {total} keepouts from {len(routes)} route(s) for {net_name}"

    def _cmd_via(self, args):
        """via <net> <x,y> — place a via explicitly at a position."""
        if len(args) < 2:
            return "ERR: usage: via <net> <x,y>"

        net_name = args[0]
        try:
            x, y = [float(v) for v in args[1].split(',')]
        except ValueError:
            return "ERR: invalid coordinates — expected x,y"

        grid, _ = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        net_id = grid.get_net_id(net_name)
        if net_id == 0:
            return f"ERR: unknown net '{net_name}'"

        board = self.viewer.board
        od = board.rules.via_od
        id_ = board.rules.via_id

        # Add to board for persistence and rendering
        board.vias.append(type('Via', (), {
            'x': x, 'y': y, 'od': od, 'id_': id_, 'net': net_name
        })())

        # Mark on grid immediately (both layers)
        grid.mark_via(x, y, net_id)

        self._request_render()
        self._log(f"via {net_name} {x},{y} | placed od={od} id={id_}")
        return f"OK: via placed at ({x},{y}) net={net_name} od={od} id={id_}"

    def _cmd_viacheck(self, args):
        """viacheck [threshold_mm] — check all vias for pad proximity."""
        threshold = DEFAULT_THRESHOLD_MM
        if args:
            try:
                threshold = float(args[0])
            except ValueError:
                return "ERR: invalid threshold — expected a number in mm"

        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        results = check_vias(board, threshold_mm=threshold)
        return format_viacheck(results, threshold_mm=threshold)

    def _cmd_check_crowding(self, args):
        """check_crowding [threshold_mm] — rank components by clearance to nearest foreign trace."""
        threshold = DEFAULT_CLEARANCE_THRESHOLD_MM
        if len(args) >= 1:
            try:
                threshold = float(args[0])
            except ValueError:
                return "ERR: invalid threshold — expected mm value"

        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        results = check_crowding(board, threshold_mm=threshold)
        return format_crowding(results, threshold_mm=threshold)

    def _cmd_check_crowding_pads(self, args):
        """check_crowding_pads [threshold_mm] — rank components by nearest foreign-net pad distance."""
        threshold = DEFAULT_PAD_THRESHOLD_MM
        if len(args) >= 1:
            try:
                threshold = float(args[0])
            except ValueError:
                return "ERR: invalid threshold — expected mm value"

        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        results = check_pad_crowding(board, threshold_mm=threshold)
        return format_pad_crowding(results, threshold_mm=threshold)

    def _cmd_check_ratsnest(self, args):
        """check_ratsnest [threshold_mm] — find foreign pads blocking ratsnest lines."""
        threshold = DEFAULT_RATSNEST_THRESHOLD_MM
        if len(args) >= 1:
            try:
                threshold = float(args[0])
            except ValueError:
                return "ERR: invalid threshold — expected mm value"

        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        results = check_ratsnest(board, threshold_mm=threshold)
        return format_ratsnest(results, threshold_mm=threshold)

    def _cmd_force(self, args):
        """force [ref] — show attraction force vector for a component, or all components."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        if len(args) >= 1:
            result = compute_force(board, args[0])
            return format_force(result)
        else:
            results = compute_force_all(board)
            return format_force_all(results)

    def _cmd_repulsion(self, args):
        """repulsion [ref] [threshold_mm] — show repulsion from foreign ratsnest lines."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        threshold = DEFAULT_REPULSION_THRESHOLD_MM
        ref = None

        for a in args:
            try:
                threshold = float(a)
            except ValueError:
                ref = a

        if ref:
            result = compute_repulsion(board, ref, threshold_mm=threshold)
            return format_repulsion(result)
        else:
            results = compute_repulsion_all(board, threshold_mm=threshold)
            return format_repulsion_all(results)

    def _cmd_pressure(self, args):
        """pressure [ref] [threshold_mm] — show foreign pad pressure around a component."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        threshold = DEFAULT_PRESSURE_THRESHOLD_MM
        ref = None

        for a in args:
            try:
                threshold = float(a)
            except ValueError:
                ref = a

        if ref:
            result = compute_pressure(board, ref, threshold_mm=threshold)
            return format_pressure(result)
        else:
            results = compute_pressure_all(board, threshold_mm=threshold)
            return format_pressure_all(results)

    def _cmd_keepouts(self, args):
        """keepouts reload|clear|save|status — manage grid keepout overlay."""
        if len(args) < 1:
            return "ERR: usage: keepouts reload|clear|save|status"

        grid, routeset = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"

        sub = args[0].lower()
        import numpy as np

        if sub == 'reload':
            for layer in (0, 1):
                grid.occupy[layer][grid.occupy[layer] == KEEPOUT_NET_ID] = 0
            self.keepouts_data = None
            n = self._load_keepouts_file(grid)
            self._log(f"keepouts reload | {n} cells loaded")
            return f"OK: reloaded {n} keepout cells from file"

        elif sub == 'clear':
            count = 0
            for layer in (0, 1):
                mask = grid.occupy[layer] == KEEPOUT_NET_ID
                count += int(np.count_nonzero(mask))
                grid.occupy[layer][mask] = 0
            self.keepouts_data = None
            return f"OK: cleared {count} keepout cells from grid"

        elif sub == 'save':
            n = self._save_keepouts_file()
            path = self._keepouts_path()
            return f"OK: saved to {path} ({n} entries)"

        elif sub == 'status':
            counts = []
            for layer in (0, 1):
                c = int(np.count_nonzero(grid.occupy[layer] == KEEPOUT_NET_ID))
                counts.append(c)
            path = self._keepouts_path()
            exists = "yes" if path and os.path.exists(path) else "no"
            n_comp = len(self.keepouts_data.get('components', {})) if self.keepouts_data else 0
            return (f"OK: keepouts on grid: F.Cu={counts[0]} B.Cu={counts[1]} "
                    f"total={counts[0]+counts[1]}, components={n_comp}, file={exists}")

        else:
            return "ERR: usage: keepouts reload|clear|save|status"

    def _cmd_move(self, args):
        """move <ref> <x,y> [r<rot>] — move/rotate a component."""
        if len(args) < 2:
            return "ERR: usage: move <ref> <x,y> [r<rot>]"

        ref = args[0]
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        # Find footprint
        fp = None
        for f in board.footprints:
            if f.ref == ref:
                fp = f
                break
        if not fp:
            return f"ERR: unknown component {ref}"

        # Parse position
        try:
            x, y = [float(v) for v in args[1].split(',')]
        except ValueError:
            return "ERR: position must be x,y"

        # Parse optional rotation
        rot = fp.rotation
        if len(args) > 2 and args[2].startswith('r'):
            try:
                rot = int(args[2][1:])
            except ValueError:
                return "ERR: rotation must be r0, r90, r180, or r270"
            if rot not in (0, 90, 180, 270):
                return "ERR: rotation must be r0, r90, r180, or r270"

        old_x, old_y, old_rot = fp.x, fp.y, fp.rotation
        fp.x = x
        fp.y = y
        fp.rotation = rot

        # Recompute absolute pad positions
        from dpcb_viewer import AbsPad, rotate_pad
        pad_def = board.pad_defs.get(fp.footprint, [])
        fp.abs_pads = []
        for pad in pad_def:
            rx, ry = rotate_pad(pad.dx, pad.dy, fp.rotation)
            fp.abs_pads.append(AbsPad(num=pad.num, x=fp.x + rx, y=fp.y + ry, pad_type=pad.pad_type))

        # Invalidate grid — will rebuild on next command
        self.grid = None
        self.routeset = None

        self._request_render()
        self._log(f"move {ref} ({old_x},{old_y}):r{old_rot} -> ({fp.x},{fp.y}):r{fp.rotation}")
        return (f"OK: moved {ref} from ({old_x},{old_y}):r{old_rot} "
                f"to ({fp.x},{fp.y}):r{fp.rotation}")

    def _cmd_probe(self, args):
        """probe <x,y> — show grid cell values at a position (mm)."""
        if len(args) < 1:
            return "ERR: usage: probe <x,y>"
        try:
            x, y = [float(v) for v in args[0].split(',')]
        except ValueError:
            return "ERR: coordinates must be x,y"
        grid, _ = self._ensure_grid()
        if grid is None:
            return "ERR: no board loaded"
        gx, gy = grid.mm_to_grid(x, y)
        if not grid.in_bounds(gx, gy):
            return f"ERR: ({gx},{gy}) out of bounds"
        fcu = int(grid.occupy[0][gy, gx])
        bcu = int(grid.occupy[1][gy, gx])
        in_keepout = (gx, gy) in grid.pad_keepout
        pad_layer = grid.pad_layers.get((gx, gy), 'none')
        return (f"OK: ({x},{y}) grid=({gx},{gy}) "
                f"F.Cu={fcu} B.Cu={bcu} pad_keepout={in_keepout} pad_layer={pad_layer}")

    def _cmd_get_vias(self):
        """get_vias — list all vias with position and net."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"
        if not board.vias:
            return "OK: 0 vias"
        lines = [f"OK: {len(board.vias)} via(s)"]
        for v in board.vias:
            lines.append(f"  ({v.x},{v.y}) {v.net}")
        return '\n'.join(lines)

    def _cmd_get_transitions(self, args):
        """get_transitions [tolerance] — find layer transitions in routes."""
        board = self.viewer.board
        if not board:
            return "ERR: no board loaded"

        tol = 0.15
        if args:
            try:
                tol = float(args[0])
            except ValueError:
                return "ERR: tolerance must be a number in mm"

        # Build endpoint sets per net per layer
        from collections import defaultdict
        endpoints = defaultdict(lambda: defaultdict(set))
        for trk in board.tracks:
            if not trk.net:
                continue
            endpoints[trk.net][trk.layer].add((round(trk.x1, 2), round(trk.y1, 2)))
            endpoints[trk.net][trk.layer].add((round(trk.x2, 2), round(trk.y2, 2)))

        # Find points where both layers have endpoints within tolerance
        transitions = []
        for net, layers in endpoints.items():
            fcu = layers.get('F.Cu', set())
            bcu = layers.get('B.Cu', set())
            for fx, fy in fcu:
                for bx, by in bcu:
                    if abs(fx - bx) <= tol and abs(fy - by) <= tol:
                        transitions.append((net, fx, fy))
                        break

        # Build via set for comparison
        via_set = set()
        for v in board.vias:
            via_set.add((round(v.x, 2), round(v.y, 2)))

        lines = [f"OK: {len(transitions)} transition(s)"]
        for net, x, y in sorted(transitions, key=lambda t: (t[0], t[1], t[2])):
            has_via = any(abs(x - vx) <= tol and abs(y - vy) <= tol for vx, vy in via_set)
            status = "VIA" if has_via else "MISSING"
            lines.append(f"  {status} ({x},{y}) {net}")
        return '\n'.join(lines)

    def _cmd_log_note(self, args):
        """log_note <text> — write a free-form note to the design log."""
        if not args:
            return "ERR: usage: log_note <text>"
        text = ' '.join(args)
        self._log(f"NOTE: {text}")
        return "OK: logged"

    def _cmd_discipline(self):
        return f"OK: {discipline_prompt()}"

    def _cmd_help(self):
        return ("OK: commands:\n"
                "  route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto]\n"
                "  route_tap <net> <x,y> [F.Cu|B.Cu|auto] [margin=N]\n"
                "  via <net> <x,y>\n"
                "  viacheck [threshold_mm]\n"
                "  check_crowding [margin_mm] [ratio]\n"
                "  check_crowding_pads [threshold_mm]\n"
                "  check_ratsnest [threshold_mm]\n"
                "  force [ref]\n"
                "  repulsion [ref] [threshold_mm]\n"
                "  pressure [ref] [threshold_mm]\n"
                "  unroute <net>\n"
                "  pads <net>\n"
                "  waypoints <net>\n"
                "  pushout <net> [amount]\n"
                "  clearkeepouts <net>\n"
                "  keepouts reload|clear|save|status\n"
                "  get_vias\n"
                "  get_transitions [tolerance]\n"
                "  log_note <text>\n"
                "  move <ref> <x,y> [r<rot>]\n"
                "  load <filename>\n"
                "  save <filename>\n"
                "  status\n"
                "  nets\n"
                "  help\n"
                "  quit")

    # ============ INTERNAL ============

    def _log(self, text):
        """Log a design step to the board's .design.log file."""
        path = getattr(self.viewer, 'board_path', None)
        if path:
            base, _ = os.path.splitext(path)
            design_log(base, text)

    def _ensure_grid(self):
        """Build or return the router grid and routeset from current board state."""
        if self.grid is not None:
            return self.grid, self.routeset

        board = self.viewer.board
        if not board:
            return None, None

        clearance = max(1, int(round(board.rules.clearance / GRID_PITCH)))
        via_od = max(1, int(round(board.rules.via_od / GRID_PITCH)))
        via_id = max(1, int(round(board.rules.via_id / GRID_PITCH)))
        track_w = max(1, int(round(board.rules.track / GRID_PITCH)))

        self.grid = RouterGrid(
            board.width, board.height,
            clearance_cells=clearance,
            via_od_cells=via_od,
            via_id_cells=via_id
        )
        self.grid.populate_from_board(board)
        self.routeset = RouteSet(track_width_cells=track_w)

        # Register pre-existing tracks from the .dpcb file into the RouteSet
        # so they can be unrouted via the API like any other route.
        nets_with_tracks = set()
        for trk in board.tracks:
            if trk.net:
                nets_with_tracks.add(trk.net)
        for net_name in nets_with_tracks:
            net_id = self.grid.get_net_id(net_name)
            if net_id:
                trks = [t for t in board.tracks if t.net == net_name]
                layer = 0 if trks[0].layer == 'F.Cu' else 1
                gx1 = int(round(trks[0].x1 / GRID_PITCH))
                gy1 = int(round(trks[0].y1 / GRID_PITCH))
                gx2 = int(round(trks[-1].x2 / GRID_PITCH))
                gy2 = int(round(trks[-1].y2 / GRID_PITCH))
                self.routeset.register_route(
                    net_id,
                    (gx1, gy1, layer),
                    (gx2, gy2, layer),
                )

        n = self._load_keepouts_file(self.grid)
        print(f"[API] Grid built: {self.grid.stats()}, {n} keepout cells loaded")
        return self.grid, self.routeset

    def _path_waypoints(self, path, grid):
        """Extract turning points from a path and format as mm coordinates."""
        if len(path) < 2:
            x, y = grid.grid_to_mm(path[0][0], path[0][1])
            layer = 'F.Cu' if path[0][2] == 0 else 'B.Cu'
            return f"({x:.1f},{y:.1f}){layer}"

        layers = ['F.Cu', 'B.Cu']
        points = []
        # Always include start
        sx, sy = grid.grid_to_mm(path[0][0], path[0][1])
        points.append(f"({sx:.1f},{sy:.1f})")

        for i in range(1, len(path) - 1):
            # Layer change = via
            if path[i][2] != path[i - 1][2]:
                vx, vy = grid.grid_to_mm(path[i][0], path[i][1])
                points.append(f"VIA({vx:.1f},{vy:.1f})")
                continue
            # Direction change = turning point
            if i >= 2 and path[i][2] == path[i - 1][2] == path[i - 2][2]:
                dx = path[i][0] - path[i - 1][0]
                dy = path[i][1] - path[i - 1][1]
                pdx = path[i - 1][0] - path[i - 2][0]
                pdy = path[i - 1][1] - path[i - 2][1]
                if dx != pdx or dy != pdy:
                    wx, wy = grid.grid_to_mm(path[i - 1][0], path[i - 1][1])
                    points.append(f"({wx:.1f},{wy:.1f})")

        # Always include end
        ex, ey = grid.grid_to_mm(path[-1][0], path[-1][1])
        layer = layers[path[-1][2]]
        points.append(f"({ex:.1f},{ey:.1f})")

        return ' -> '.join(points)

    def _apply_track_output(self, net_name, output):
        """Add TrackOutput segments/vias to the board for the viewer."""
        board = self.viewer.board
        if not board:
            return

        existing = {(t.x1, t.y1, t.x2, t.y2, t.layer, t.net)
                    for t in board.tracks if t.net == net_name}

        for t in output.tracks:
            key_fwd = (t.x1_mm, t.y1_mm, t.x2_mm, t.y2_mm, t.layer, t.net)
            key_rev = (t.x2_mm, t.y2_mm, t.x1_mm, t.y1_mm, t.layer, t.net)
            if key_fwd in existing or key_rev in existing:
                continue
            existing.add(key_fwd)
            board.tracks.append(type('Track', (), {
                'x1': t.x1_mm, 'y1': t.y1_mm,
                'x2': t.x2_mm, 'y2': t.y2_mm,
                'width': t.width_mm,
                'layer': t.layer,
                'net': t.net
            })())

        for v in output.vias:
            board.vias.append(type('Via', (), {
                'x': v.x_mm, 'y': v.y_mm,
                'od': v.od_mm, 'id_': v.id_mm,
                'net': v.net
            })())

    def _remove_tracks_from_board(self, net_name, path_index, grid):
        """Remove tracks for a net from the board object."""
        board = self.viewer.board
        if not board:
            return
        board.tracks = [t for t in board.tracks if t.net != net_name]
        board.vias = [v for v in board.vias if v.net != net_name]

    def _keepouts_path(self):
        path = getattr(self.viewer, 'board_path', None)
        if not path:
            return None
        base, _ = os.path.splitext(path)
        return base + '.keepouts.json'

    def _keepouts_legacy_path(self):
        path = getattr(self.viewer, 'board_path', None)
        if not path:
            return None
        base, _ = os.path.splitext(path)
        return base + '.keepouts'

    def _resolve_component_cells(self, comp_data, fp):
        stored_rot = comp_data.get('rotation', 0)
        cur_rot = getattr(fp, 'rotation', 0)
        gx_c = int(round(fp.x / GRID_PITCH))
        gy_c = int(round(fp.y / GRID_PITCH))
        cells = []
        for layer, dx, dy in comp_data.get('cells', []):
            if stored_rot != cur_rot:
                continue
            cells.append((layer, gx_c + dx, gy_c + dy))
        return cells

    def _apply_keepouts_data(self, grid, board):
        if not self.keepouts_data:
            return 0
        count = 0
        for entry in self.keepouts_data.get('board', []):
            layer, gx, gy = entry[0], entry[1], entry[2]
            if grid.in_bounds(gx, gy) and layer in (0, 1):
                grid.occupy[layer][gy, gx] = KEEPOUT_NET_ID
                count += 1
        fp_map = {fp.ref: fp for fp in board.footprints}
        for ref, comp_data in self.keepouts_data.get('components', {}).items():
            fp = fp_map.get(ref)
            if not fp:
                continue
            for layer, gx, gy in self._resolve_component_cells(comp_data, fp):
                if grid.in_bounds(gx, gy) and layer in (0, 1):
                    grid.occupy[layer][gy, gx] = KEEPOUT_NET_ID
                    count += 1
        return count

    def _load_keepouts_file(self, grid):
        import json
        board = self.viewer.board
        path = self._keepouts_path()
        if path and os.path.exists(path):
            with open(path, 'r') as f:
                self.keepouts_data = json.load(f)
            return self._apply_keepouts_data(grid, board)
        legacy = self._keepouts_legacy_path()
        if legacy and os.path.exists(legacy):
            cells = []
            with open(legacy, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split(',')
                    if len(parts) != 3:
                        continue
                    cells.append([int(parts[0]), int(parts[1]), int(parts[2])])
            self.keepouts_data = {'board': cells, 'components': {}}
            return self._apply_keepouts_data(grid, board)
        return 0

    def _save_keepouts_file(self):
        import json
        path = self._keepouts_path()
        if not path or not self.keepouts_data:
            return 0
        with open(path, 'w') as f:
            json.dump(self.keepouts_data, f, indent=2)
        n = len(self.keepouts_data.get('board', []))
        for comp_data in self.keepouts_data.get('components', {}).values():
            n += len(comp_data.get('cells', []))
        return n

    def _request_render(self):
        """Thread-safe render request to the viewer."""
        try:
            self.viewer.root.after(0, self.viewer.render)
        except:
            pass

    def _board_to_dpcb(self, board):
        """Serialise board back to .dpcb format."""
        lines = []
        lines.append(f"HDR:v1:gen=dpcb_router")
        lines.append(f"BOARD:{board.width}x{board.height}")
        lines.append(f"LAYERS:{board.layers}")
        lines.append(f"RULES:clearance={board.rules.clearance}:track={board.rules.track}:via={board.rules.via_od}/{board.rules.via_id}")

        for fp in board.footprints:
            rot = f":r{fp.rotation}" if fp.rotation else ""
            lines.append(f"FP:{fp.ref}:{fp.lib}:{fp.footprint}@({fp.x},{fp.y}){rot}")

        # Pad definitions
        written_pads = set()
        for fp in board.footprints:
            key = fp.footprint
            if key in written_pads:
                continue
            if key in board.pad_defs:
                pads = board.pad_defs[key]
                pad_strs = ','.join(f"{p.num}@({p.dx},{p.dy}):{p.pad_type}" for p in pads)
                lines.append(f"PADS:{fp.lib}:{key}:{pad_strs}")
                written_pads.add(key)

        for net in board.nets:
            pad_strs = ','.join(f"{r}.{p}" for r, p in net.pads)
            lines.append(f"NET:{net.name}:{pad_strs}")

        for trk in board.tracks:
            lines.append(f"TRK:({trk.x1},{trk.y1})->({trk.x2},{trk.y2}):{trk.width}:{trk.layer}:{trk.net}")

        for via in board.vias:
            lines.append(f"VIA:({via.x},{via.y}):{via.od}/{via.id_}:{via.net}")

        return '\n'.join(lines) + '\n'
    