# PCB ROUTING
# Last updated: 2026-03-17
# Project: AI-Driven KiCad PCB Design Pipeline


## OVERVIEW

Routing is performed by dpcb_router.py, a grid-based A* pathfinder
that runs inside the viewer (dpcb_viewer.py). The AI communicates
with the router via a TCP socket API served by dpcb_api.py.

The AI's role is STRATEGY — which nets to route, in what order,
on which layer, and when to rip up and retry. The router handles
GEOMETRY — finding a legal path between two points on the grid.

The AI does not compute paths or coordinates for intermediate
track segments. It gives the router two endpoints and a layer
preference. The router does the rest.


## TOOL ARCHITECTURE

Three Python modules, one process:

```
dpcb_viewer.py   — GUI, holds board state in memory, renders live
dpcb_router.py   — A* pathfinder, grid management, numpy-accelerated
dpcb_api.py      — TCP socket server, command parsing, runs as thread in viewer
```

Start:
```bash
python dpcb_viewer.py board.dpcb [--port 9876]
```

The viewer opens, loads the board, builds the routing grid, and
listens on TCP port 9876 (default). The AI sends text commands
and receives text responses.

### Connecting from inside Docker

The viewer runs on the HOST, not inside the container. Do NOT
connect to localhost — it will be refused.

The host is reachable at the Docker gateway IP:
```python
import socket

HOST = '172.17.0.1'
PORT = 9876

def send_cmd(sock, cmd):
    """Send a command and read until end-of-response marker."""
    sock.sendall((cmd + '\n').encode())
    buf = ''
    while True:
        buf += sock.recv(4096).decode()
        if '\n.\n' in buf:
            return buf[:buf.index('\n.\n')]

s = socket.create_connection((HOST, PORT), timeout=5)
print(send_cmd(s, 'status'))
s.close()
```

To verify the gateway IP if it changes:
```bash
python3 -c "
import struct, socket
with open('/proc/net/route') as f:
    for line in f:
        fields = line.strip().split()
        if fields[1] == '00000000':  # default route
            gw = struct.pack('<I', int(fields[2], 16))
            print(socket.inet_ntoa(gw))
            break
"
```

### load command path

Always use the ABSOLUTE HOST PATH. The `.keepouts` file is
loaded automatically when the `.dpcb` is loaded — it must be
in the same directory with the same basename.

Host path root: `/home/<user>/projects/pcb_design/`
Container path root: `/workspace/`  (these are the same directory)

```
load /home/<user>/projects/pcb_design/demo_2_7seg/test2_clean.dpcb
```

DO NOT use `/workspace/...` paths — they don't exist on the host
and fail silently (API returns `OK: loading ...` but `status`
shows `no board loaded`, keepouts not loaded).


## ROUTER INTERNALS

Grid pitch: 0.1mm. A 100x80mm board = 1000x800 cells per layer.
Two layers: F.Cu (front copper) and B.Cu (back copper).

All coordinates in the .dpcb file and API commands are in mm.
The router snaps to the nearest 0.1mm grid cell internally.

### Clearance enforcement

Obstacles (existing tracks, pads, vias belonging to other nets)
are dilated on the grid by (half_track_width + clearance). This
means A* only checks single cells — track width and clearance
are pre-computed into the blocked grid. A cell is passable for
net N if no foreign-net obstacle (after dilation) covers it.

### Pad keep-out

Pads are marked on the grid with a radius of 0.4mm (4 cells).
This is large enough to prevent routing between adjacent IC pins
on fine-pitch packages (e.g. TSSOP at 0.65mm pitch). The router
must go around IC pin rows, not between them.

Through-hole pads are marked on BOTH layers (they are plated
through the board). SMD pads are marked on their placement layer.

### Via handling

When layer_mode is 'auto', the router can insert vias to
transition between F.Cu and B.Cu. Via cost is penalised in the
A* heuristic so the router prefers single-layer routes but will
via when necessary. Via footprint (outer diameter) is checked
for clearance on both layers before placement.

### Performance

Typical route times (100x80mm board, numpy-accelerated):
- Short route (5mm):     ~20ms
- Medium route (30mm):   ~200-400ms
- Long cross-board (80mm): ~300-600ms

Fast enough for iterative route/unroute cycles.


## API COMMANDS

Connect via TCP:
```bash
echo "command" | nc localhost 9876
```

Or hold a persistent connection for multiple commands (one per line).

### Response protocol

Every response ends with `\n.\n` — a dot on its own line (SMTP/FTP
convention). Clients MUST read until they see `\n.\n` to know the
response is complete. This allows multi-line responses (e.g. `nets`,
`pads`, `viacheck`) to contain internal newlines without ambiguity.

```
<response line 1>\n
<response line 2>\n
.\n
```

Do NOT use fixed-delay sleeps to guess when a response is finished.

### route

```
route <net> <x1,y1> <x2,y2> [F.Cu|B.Cu|auto] [margin=N]
```

Route a track from (x1,y1) to (x2,y2) for the named net.
Coordinates are absolute positions in mm — typically pad centres
read from the .dpcb file or computed from footprint position + pad
offsets + rotation.

Layer modes:
- `F.Cu`  — route on front copper only
- `B.Cu`  — route on back copper only
- `auto`  — router chooses, may insert vias (default)

Margin (optional):
- `margin=N` — dilation around existing tracks in grid cells (0.1mm each)
- Default is 1 cell (0.1mm) if not specified
- Higher values force more clearance from foreign-net tracks
- Example: `margin=3` gives 0.3mm clearance from existing tracks

Response:
```
OK: Routed: 14.3mm, 0 vias, 3 segs
  path: (21.5,13.9) -> (17.5,13.9) -> (17.5,50.0) -> (21.7,57.1)
FAIL: No path found (245000 iters)
```

On success, the response includes the path as a sequence of waypoints
(turning points and vias in mm). Use this to verify the trace runs
where expected — e.g. not parallel to an IC pin row. If the path
looks bad, unroute immediately and re-route with waypoints.

The AI must know the pad coordinates. These are computed from:
```
abs_x = fp_x + rotated_dx
abs_y = fp_y + rotated_dy
```
Using the rotation rules in PCB_FORMAT_DPCB.md.

### unroute

```
unroute <net> [path_index]
```

Remove all routed tracks for a net, or a specific path segment.
Frees the grid cells so other nets can use the space.

Response:
```
OK: unrouted 3 path(s) for /clk
```

### status

```
status
```

Returns board dimensions, component/net/track counts, and grid
utilisation.

Response:
```
OK: 100.0x80.0mm fps=24 nets=54 trks=141 vias=56 grid=1000x800 fcu=2.2% bcu=2.7%
```

### nets

```
nets
```

Lists all nets with their pad connections and routed status.

Response:
```
OK: 54 nets
  VCC: J1.1,U1.16,U2.16,C1.1,C2.1,... [23trk]
  GND: J1.2,U1.8,U2.8,C1.2,C2.2,... [18trk]
  /clk: J1.5,U1.3,U2.3 [unrouted]
  ...
```

### pads

```
pads <net>
```

Show absolute pad positions for a net. Returns computed coordinates
(footprint position + rotated pad offset) — no manual calculation needed.

Response:
```
OK: GND pads:
  C1.2 @ (19.0,27.95)
  R1.2 @ (15.975,42.0875)
  U3.1 @ (15.0,37.8625)
  ...
```

### probe

```
probe <x,y>
```

Show grid cell state at a position (mm). Reports net occupancy on
both layers, pad keepout status, and pad layer assignment.

Response:
```
OK: (15.0,35.0) grid=(150,350) F.Cu=0 B.Cu=0 pad_keepout=False pad_layer=none
```

Useful for debugging blocked routes — check what's occupying cells
around a pad that won't route.

### waypoints

```
waypoints <net>
```

Show waypoints for routed paths of a net. Requires the net to have
existing routes.

### pushout

```
pushout <net> [amount]
```

Push routed traces outward from obstacles. Requires the net to have
existing routes.

### clearkeepouts

```
clearkeepouts <net>
```

Clear keepout cells from routed paths of a net.

### move

```
move <ref> <x,y> [r<rot>]
```

Move a component to a new position. Optionally change rotation.
Updates the grid immediately — no reload needed.

Response:
```
OK: moved C1 from (15.975,27.0):r90 to (19.0,27.0):r90
```

### save

```
save <filename>
```

Write the current board state (including new routes) to a .dpcb file.

### load

```
load <filename>
```

Load a .dpcb file. Rebuilds the routing grid.

### help

```
help
```

Lists available commands.

### keepouts

```
keepouts reload|clear|save|status
```

Manage grid keepout zones — cells blocked for all nets.

- `keepouts reload` — re-read from `.keepouts` file and apply to grid
- `keepouts clear`  — remove all keepout cells from grid
- `keepouts save`   — write current grid keepouts to file
- `keepouts status` — show keepout cell count per layer

Keepout zones are stored in a `.keepouts` file alongside the `.dpcb`
file (same basename, e.g. `test2_clean.keepouts` for `test2_clean.dpcb`).
The file is auto-loaded when the `.dpcb` is loaded.

File format: CSV, one grid cell per line. Comments start with `#`.
```
# layer, gx, gy  (grid coords, 0.1mm pitch)
# layer 0 = F.Cu, layer 1 = B.Cu
0,138,328
0,138,329
1,200,400
```

Typical use: define exclusion zones around IC bodies and pad approach
channels to prevent routes from cutting through component packages.
These are manual courtyard/body exclusions on the routing grid.


## ROUTING STRATEGY

The AI decides strategy. The router executes geometry.

### Route planning order

1. **Short 2-pad nets first** — decoupling caps, pull-up/down resistors,
   local connections. These are easy wins that establish a routing
   scaffold and claim minimal space.

2. **Power nets (VCC, GND)** — route as buses or rails. On 2-layer
   boards, GND often runs on B.Cu as a bus or pour. VCC on F.Cu
   with via taps to B.Cu where needed.

3. **Signal buses** — clock, data, strobe, enable. Route in parallel
   where possible. These often run long distances across the board.

