4. **Remaining nets** — complex multi-pad nets, most constrained last.

### Multi-pad nets

Nets with more than 2 pads require multiple route commands. The AI
breaks the net into point-to-point segments:

```
# NET /clk: J1.5, U1.3, U2.3
# Route as a chain: J1.5 -> U1.3 -> U2.3

route /clk 3.0,20.16 21.525,10.095 F.Cu
route /clk 21.525,10.095 79.525,10.095 F.Cu
```

The AI chooses the chaining order based on physical layout — typically
following signal flow or shortest spanning tree.

### Layer strategy

**General 2-layer approach:**
- Route most traces on F.Cu
- Use B.Cu when F.Cu is blocked
- Use `auto` to let the router find a path with vias when needed
- Power buses often work well on B.Cu with via taps up to F.Cu

**Through-hole boards:**
- TH pads connect on both layers — a trace can arrive at a TH pad
  on either layer without needing an explicit via
- The pad itself acts as a free via
- IC channel (space between DIP pin columns) can carry B.Cu traces

**SMD boards:**
- SMD pads only exist on their placement layer
- Vias are needed for every layer transition
- Keep via count low — each via costs board space on both layers

### Waypoint routing

The router finds the shortest legal path between two points. But
sometimes the shortest path creates problems — it may crowd a
corridor needed by another net, or approach a pad from an awkward
angle. **Waypoint routing** solves this by breaking a single route
into sub-segments via intermediate points.

The AI decides WHERE the trace should go, then issues multiple
route commands to guide it through those waypoints:

```
# Direct route from pad A to pad B would crowd pad C's corridor.
# Instead, route via a waypoint that pulls the trace wide:

route NET pad_A 10.0,32.1 F.Cu          # step 1: pad A → waypoint
route NET 10.0,32.1 10.0,44.0 F.Cu      # step 2: waypoint → waypoint
route NET 10.0,44.0 pad_B F.Cu          # step 3: waypoint → pad B
```

**When to use waypoints:**

- **Corridor conflicts** — two nets need the same narrow corridor.
  Pull one net wide via waypoints so both fit.
- **Pad approach control** — a trace must approach a pad from a
  specific direction (e.g. from the south, not through a neighbour).
  Use a waypoint to establish the approach angle.
- **Keepout navigation** — guide a trace around keepout zones
  when the direct path would fail or produce ugly detours.
- **Reserving space** — route a net through waypoints that stay
  clear of an area you know a later net will need.

**Key principles:**

1. Waypoints are ordinary route commands — each sub-segment is
   a legal routed track with its own clearance and dilation.
2. The router treats each sub-segment independently. It doesn't
   know about your strategic intent — that's the AI's job.
3. Fewer waypoints is better. Use the minimum needed to express
   your routing intent. Each extra segment adds track length.
4. Waypoint coordinates don't need to be pad centres. Any grid
   position works — the router snaps to the nearest 0.1mm cell.

**Real example — TSSOP-8 with two nets sharing a side:**

U3 has ILIM on pad 5 and VM on pad 4, both on the same side.
Direct routing of ILIM blocks VM's only corridor to pad 4.

Solution: route ILIM via waypoints far to the left (x=10.0),
then down and back in to R1. This keeps ILIM's trace well clear
of pad 5's approach corridor, leaving room for VM.

```
# ILIM: pad 5 → waypoint left → waypoint south → R1 pad 1
route Net-(U3-ILIM) 14.025,32.1375 10.0,32.1 F.Cu
route Net-(U3-ILIM) 10.0,32.1 10.0,44.0 F.Cu
route Net-(U3-ILIM) 10.0,44.0 15.975,43.9125 F.Cu

# VM: now has a clear corridor to pad 4
route Net-(U3-VM) 15.975,26.05 14.025,37.8625 F.Cu
```

Without waypoints, ILIM would take the direct path right past
pad 4, blocking VM. With waypoints, both nets route cleanly.

### Adjust placement before forcing routes

When a route is difficult — high segment count, hugging keepouts,
boxed-in pads — the first question is: **can I move the component
to make the route easy?**

Placement and routing are iterative. Component positions are not
fixed constraints. A decoupling cap that's in the path of signal
traces can be shifted a few mm to clear the corridor. An 0805
resistor blocking a pad exit can be repositioned.

Signs that placement needs adjusting (not routing tricks):
- A pad has no free exit cells because adjacent traces box it in
- A short route (< 5mm) produces many segments (hugging obstacles)
- Waypoints are needed just to escape a component's own neighbourhood
- Routing order matters (net A must route before net B or B fails)

Moving a component 2mm to the right is always better than a
complex waypoint strategy to work around a bad position.

**Before moving, check the netlist.** Look at where each pad
on the component connects. Move toward the connections:
- If pad 1 connects to a pad on the right, move right
- If pad 2 connects to GND bus on the bottom, don't move it
  further from the bus
- The netlist tells you WHICH direction to move, not just
  that movement is needed

This is not a heuristic — it's basic data. Always check net
connections before proposing a component move.

### Component corridor check

After routing several nets, scan for components that are being
navigated around by multiple unrelated traces. This is a reliable
sign of a placement problem — the component is sitting in a
routing corridor it should not occupy.

**Symptoms:**
- Two or more traces with high segment counts that all detour
  through the same region
- Traces taking a wide arc around a passive component
- A short route (< 5mm end-to-end) producing many segments

**Diagnosis:**
1. Note which component the traces are detouring around
2. Check its net connections with `pads <net>` — where does it
   actually need to connect?
3. If all its connections are to one side, it can likely move
   to the other side of the corridor without penalty

**Fix:**
1. `unroute` all traces affected by the component's position
2. `move <ref>` to shift the component out of the corridor
3. Re-route the affected traces

The improvement is often dramatic — traces that required 10+
segments to navigate around the component may route cleanly
in 2 segments once it moves. This cannot be replicated by
any routing trick.

**Real example — R1 at (15.975, 27.0):**

/sr1_qp0 and /sr1_qp1 needed to approach U3's top pins at
x≈14.7 and x≈15.3. R1 sat at x=15.975 — directly in that
corridor. Both traces had to detour around it.

```
# Before: R1 blocking corridor
route /sr1_qp0 ... 14.675,32.1375   → 27.5mm, 10 segs
route /sr1_qp1 ... 15.325,32.1375   → 25.7mm, 11 segs

# After: move R1 left, out of the U3 approach corridor
move R1 11.0,27.0 r270
route /sr1_qp0 ... 14.675,32.1375   → 27.5mm, 2 segs
route /sr1_qp1 ... 15.325,32.1375   → (clean)
```

R1's own connection (ILIM → U3.4) still routes cleanly because
R1 moved toward U3's left edge, not away from it.

### Rip-up and retry

When a route fails:
1. Check what's blocking the path (other nets claiming the corridor)
2. Unroute one or more blocking nets
3. Re-route the failed net first (it now has priority)
4. Re-route the displaced nets with `auto` layer mode

This is the core intelligence loop. The AI must decide WHICH net
to sacrifice and WHETHER the new arrangement is better overall.

```
# /oe failed on F.Cu — /clk is blocking
unroute /clk
route /oe 3.0,15.08 26.475,8.825 F.Cu
route /clk 3.0,20.16 21.525,10.095 auto    # may via to B.Cu
```

### When to use auto vs explicit layer

- **Start with explicit** (F.Cu) for short, simple nets
- **Use auto** when explicit fails, or for long routes across congested areas
- **Use B.Cu explicitly** for ground buses, or when you know the path is
  clear on B.Cu and blocked on F.Cu
- **Avoid excessive vias** — each via adds parasitic capacitance and
  uses space on both layers. Prefer single-layer routes where possible.


## IC PIN APPROACH RULES

NEVER route a trace parallel to an IC pin row within the pin approach
zone. A trace running alongside the pins blocks access to every pin
it passes. This is the primary cause of unroutable boards.

### Rules

1. **Exit perpendicular** — the first segment from an IC pin must go
   AWAY from the IC body, perpendicular to the pin row. Not along it.

2. **Clear the approach zone** — do not turn parallel until the trace
   is outside the pin keepout radius (at least 1mm from the nearest
   pin on the row).

3. **No long parallel traces** — a trace running alongside a pin
   column at close range steals the approach corridor from every pin
   between its endpoints. One parallel trace can make 4+ pins
   unreachable.

4. **Vias are escape tools** — if a trace must cross a pin row region,
   via to B.Cu BEFORE entering the zone, run underneath, and via back
   AFTER clearing it. Do not run on F.Cu through the approach zone.

5. **Fan-out pattern** — for multi-pin ICs (SOIC-16, TSSOP), traces
   from each pin should fan outward like spokes. Left-column pins fan
   left, right-column pins fan right. The fan creates space between
   traces and keeps all pin corridors open.


## FAN-OUT ROUTING PROCEDURE (MANDATORY)

Every trace from an IC pin MUST be routed in two phases. Do NOT issue
a single `route` from IC pin directly to destination — the router will
take the shortest path, which runs tight against the IC and blocks
adjacent pins.

### Phase 1: Fan-out stub

Route a short segment (3-5mm) from the IC pin **perpendicular to the
pin row**, away from the IC body. This is the fan-out stub.

- Left-column pins: stub goes LEFT (decreasing x)
- Right-column pins: stub goes RIGHT (increasing x)
- Top-row pins: stub goes UP (decreasing y)
- Bottom-row pins: stub goes DOWN (increasing y)

The stub endpoint is the **waypoint**. It must be in open board area,
clear of other IC pin zones.

### Phase 2: Trunk route

Route from the waypoint to the destination (or to another waypoint
closer to the destination). The router now has room to manoeuvre
because it starts in open space, not jammed against an IC.

### Stagger rule

When multiple pins on the same IC side fan out, their stubs MUST have
different lengths so traces don't cross. The rule is:

**Inner trace = shorter stub. Outer trace = longer stub.**

"Inner" means closer to the IC body (closer to the pin row centre).
"Outer" means further from the IC body (closer to the end of the row).

```
Pin row (left column, pins go top to bottom):

  pin 4 (outer/top)    ←————————— longest stub (e.g. 6mm)
  pin 5                ←———————   medium stub  (e.g. 5mm)
  pin 6                ←—————     shorter stub (e.g. 4mm)
  pin 7 (inner/bottom) ←———      shortest stub (e.g. 3mm)
  IC body
```

The outer trace extends further so it clears the inner traces below
it. Each trace's waypoint is further from the IC than the one below,
creating a spread:

```
  waypoint pin4 (x=15.5)  ·——————— pin 4
  waypoint pin5 (x=16.5)   ·—————— pin 5
  waypoint pin6 (x=17.5)    ·————— pin 6
  waypoint pin7 (x=18.5)     ·———— pin 7
                               |IC|
```

The inner stub is SHORT because it only needs to clear its own pin.
The outer stub is LONG because it must pass over all inner stubs
without crossing them. If the outer stub were shorter than the inner,
traces would cross at the fan-out point.

### Procedure (step by step)

```
1. Identify all pins on the IC side that need routing
2. Order them by distance to their destinations
3. For each pin (closest destination first):
   a. Compute waypoint: pin position + 3-5mm perpendicular to pin row
      (stagger length per the rule above)
   b. route <net> <pin_x,pin_y> <waypoint_x,waypoint_y> <layer>
   c. Evaluate: stub should be 1-2 segments, short length
   d. route <net> <waypoint_x,waypoint_y> <dest_x,dest_y> [auto]
   e. If route uses vias: run viacheck (mandatory)
   f. Evaluate total route quality
4. After all pins on this IC side are routed, verify no trace
   runs parallel to the pin row within the approach zone
```

### Why this is mandatory

Without fan-out, the router produces traces that:
- Run parallel to pin rows, blocking 4+ adjacent pins
- Crowd the IC body, leaving no room for later nets
- Force increasingly complex workarounds for each subsequent pin

Fan-out costs ~3-5mm of extra trace length per pin but makes every
subsequent route easier. The total board trace length is usually
SHORTER because later routes don't need long detours.

### Example: U1 left-column pins 4,5,6,7 going to U3-U6

Pins are on the left column, stubs go LEFT. Pin 4 is the outermost
(top of row), pin 7 is the innermost (bottom of row). Outer = longer
stub, inner = shorter stub.

```
# Pin 4 (outer/top): longest stub — must clear all inner stubs
route /sr1_qp0 21.525,11.365 15.5,11.365 F.Cu   # 6mm left
route /sr1_qp0 15.5,11.365 14.675,32.1375 F.Cu   # trunk to U3.3

# Pin 5: long stub
route /sr1_qp1 21.525,12.635 16.5,12.635 F.Cu   # 5mm left
route /sr1_qp1 16.5,12.635 15.325,32.1375 F.Cu   # trunk to U3.2

# Pin 6: medium stub
route /sr1_qp2 21.525,13.905 17.5,13.905 F.Cu   # 4mm left
route /sr1_qp2 17.5,13.905 <dest> auto           # trunk to U4

# Pin 7 (inner/bottom): shortest stub
route /sr1_qp3 21.525,15.175 18.5,15.175 F.Cu   # 3mm left
route /sr1_qp3 18.5,15.175 <dest> auto           # trunk to U4
```

Waypoints spread from x=15.5 (outer) to x=18.5 (inner). The outer
trace clears all inner stubs. No traces cross at the fan-out point.
All remaining pins on U1's left side still have clear approach
corridors.

### Via staggering for parallel fan-outs

When multiple traces fan out from the same IC side, their vias and
B.Cu runs must not cross each other's F.Cu fan-out segments.

1. **Maintain order** — the outer trace stays outer, the inner trace
   stays inner. No interleaving. If pin 4 fans to x=10 and pin 5
   fans to x=13, pin 5's vias must not sit at x < 10.

2. **Stagger in both dimensions** — vias on parallel B.Cu runs need
   separation in x AND y. Two vias at the same y but adjacent x will
   fail viacheck against each other's tracks.

3. **Order vias by destination** — the trace going to the outermost
   pad gets the via closest to the destination. The inner trace gets
   a via further from the destination. This ensures last-mile F.Cu
   legs don't tangle.

4. **Avoid crossing fan-out segments** — if trace A's F.Cu fan-out
   spans y=11 at x=10 to x=21, trace B's via must not sit at
   x=10..21, y≈11. Place it at a different y to avoid crossing
   trace A's segment.

### Why auto-route fails here

The A* router optimises for shortest path. The shortest path from an
IC pin to a distant target often runs parallel to the pin row — it is
geometrically shorter than fanning out first. But it blocks adjacent
pins, making their routes impossible. The AI must override the router
by using waypoints or vias to enforce the fan-out pattern.

### Fan-out helper tool

Use `helper_fanout.py` to determine correct stub order and lengths:

```
python helper_fanout.py <exit_direction> <stub_spacing> <first_lane> <pin>,<x>,<y> ...
```

- `exit_direction`: north | south | east | west — where the trunks go
- `stub_spacing`: mm between parallel trunk lanes (typically 1.0)
- `first_lane`: mm coordinate of the innermost trunk lane
- Pins: name,x,y for each pin in the fan-out group

Example:
```
python helper_fanout.py south 1.0 18.5 U1.6,21.525,13.905 U1.7,21.525,15.175

  1. U1.7  (inner) -> stub (18.5,15.175)  len=3.0mm
  2. U1.6  (outer) -> stub (17.5,13.905)  len=4.0mm
```

The tool sorts pins by proximity to the exit edge. Nearest = inner = shortest stub.
Run this BEFORE every fan-out to get the correct assignment.


## POWER ROUTING

Do NOT route power traces through IC bodies. VCC and GND must go
AROUND ICs, not between their pins.

Good patterns:
- VCC bus: horizontal trace above or below the IC, with vertical
  branches to individual power pins from the outside edge
- GND bus on B.Cu: horizontal run with via taps up to IC GND pins
- Power traces approaching IC pins from the outside edge only

Bad patterns:
- Horizontal power trace at the same y-coordinate as IC pins
- Long power trace running through the IC pin field
- Power traces threading between adjacent IC pins


## IC BODY KEEPOUTS

Before routing any board, generate solid keepout rectangles for all IC
bodies (the area between pad rows). Without these, vias and traces can
be placed inside IC packages where no copper can physically exist.

Keepouts are stored in `<board>.keepouts.json` as grid cells marked on
both layers. For each IC:
1. Compute the body rectangle from the pad extents (not the full
   footprint — the body between the two pad rows)
2. Fill all grid cells inside that rectangle on both F.Cu and B.Cu
   with keepout markers
3. Reload keepouts via `keepouts reload`

The router and viacheck will then reject any path or via inside an IC body.


## COMPONENT REPOSITIONING

When a passive component (resistor, capacitor) blocks the approach
corridor to an IC pin, move the component rather than routing around it.
Workaround traces through congested areas produce multi-segment
wiggling paths that are fragile and block future routes.

Use `move <ref> <x,y>` to reposition the component, then re-route
its net. Typical moves: shift a resistor 3mm along its axis to clear
a corridor, or move it to the opposite side of its IC.


## DESIGN QUALITY CHECKS

A set of API commands for assessing routing quality at any stage.
Each command answers "what needs attention" — the AI reasons about
why and decides what to do. Checks return a flag per item (OK or
flagged) plus the values that justify it.

Call these after each routing phase, not just at the end.

### viacheck

```
viacheck [threshold_mm]
```

Checks every via on the board for proximity to pads. Default
threshold is 2.0mm. Returns OK or FAIL per via with distance
to nearest pad.

```
OK: 7 via(s) checked, threshold=2.0mm
  OK   (36.3,16.0) /clk  nearest=U1.9 @ (26.5,16.4)  dist=9.84mm
  FAIL (37.3,31.4) /clk  nearest=U5.2 @ (37.3,32.1)  dist=0.74mm
  1 via(s) within threshold — relocate using: unroute / via / re-route
```

A FAIL means the via is too close to a pad and should be relocated
using the pre-seed via workflow (see VIA PLACEMENT CONTROL).

### check_crowding

```
check_crowding [threshold_mm]
```

For every component, measures the clearance to the nearest foreign-net
track segment. Results are sorted closest-first so the most crowded
components appear at the top. Components below the threshold are flagged
CROWDED.

- `threshold_mm` — clearance below which a component is flagged (default 1.0mm)

```
OK: 24 component(s) checked, 2 crowded  (threshold=1.0mm)
  CROWDED  C1   (15.975,43.0)  clearance=0.31mm  nearest=/sr1_qp2
  CROWDED  C3   (37.975,43.0)  clearance=0.28mm  nearest=/sr1_qp6
  ok       R1   (11.0,27.0)    clearance=1.84mm  nearest=/sr1_qp0
  ok       U1   (21.525,...)   clearance=4.10mm  nearest=/clk
  ...
```

Results are sorted by clearance ascending — smallest clearance first.
The AI scans from the top, assesses flagged components, and decides
whether repositioning would help using `pads <net>` for context.

## VERIFICATION

After routing, save the board and run verification:

```bash
save routed_board.dpcb
# Then in another terminal:
python3 check_dpcb.py routed_board.dpcb
```

Fix any violations, re-route as needed, verify again. Do not
proceed to gen_pcb.py until check_dpcb.py returns clean (exit 0).

Priority of problems (worst first):
1. Short circuits (trace through wrong-net pad)
2. Same-layer crossings (trace-vs-trace)
3. Collinear conflicts
4. Unconnected nets
5. Dangling endpoints
6. DRC violations (clearance)
7. Unnecessarily long routes
8. Aesthetics


## VIA PLACEMENT CONTROL

### The problem with auto-placed vias

The A* router places vias wherever the path happens to need a layer
transition. It uses the normal routing margin (small clearance) when
deciding via positions — it has no larger "via keepout" around pads.
Result: vias can land uncomfortably close to pads.

### Why the router can't fix this internally

A* only attempts a via when it hits a blocked boundary — meaning it
is already hugging an obstacle. Any larger clearance check applied
at that moment will always fail: the router is positioned right at
the edge of something. A* never navigates to a position that is
2mm clear of everything unless told to. The problem cannot be solved
by changing the blocked grid alone.

### The solution: pre-seed vias at good positions

The `via` command places a via explicitly at any position before
routing. A via marked on the grid occupies BOTH layers with the
net_id. A* treats it as a passable bridge — it routes TO it on one
layer and FROM it on the other naturally.

This gives the AI full control over via placement:

```
# 1. Auto-route — router places vias wherever it can
route /clk 21.525,10.095 79.525,10.095 auto margin=3

# 2. Inspect — via landed close to a pad. Unroute.
unroute /clk

# 3. Pre-seed a via at a better position (clear of pads)
via /clk 50.0,15.0

# 4. Re-route in two legs — router uses the pre-seeded via
route /clk 21.525,10.095 50.0,15.0 F.Cu margin=3
route /clk 50.0,15.0 79.525,10.095 auto margin=3
```

### Via placement procedure (MANDATORY)

Place and verify each via BEFORE routing any track to it.

```
1. Choose via position (>= 2mm from any existing entity)
2. via <net> <x,y>
3. viacheck
4. If FAIL: remove via (unroute <net>), choose new position, go to 2
5. If PASS: route the track leg to/from this via
6. viacheck again after routing (track may have moved near other vias)
7. If FAIL: unroute, relocate via, go to 2
```

Do NOT batch multiple vias or route legs before checking. Each via
is verified clean before any track is committed to it.

Minimum spacing rules:
- Via to foreign-net track: >= 2mm (viacheck threshold)
- Via to foreign-net via: >= 2mm
- Via to any pad: >= 2mm
- Parallel B.Cu traces from same IC: >= 2mm apart in x

### WARNING: unroute clears pre-seeded vias

`unroute <net>` removes ALL grid cells for the net — tracks AND vias.
Pre-seeded vias are indistinguishable from auto-placed vias on the
grid, so they are swept up too. If you unroute a net that has
pre-seeded vias, you must re-place them after unrouting.

Safe order when retrying a via route:
1. `unroute <net>`
2. `via <net> <x,y>` — re-place the via(s)
3. Route each leg again

Alternatively, place vias only after the first leg is routed, not
before. This avoids losing them to an unroute cycle.

### Key insight

A via on the grid is just net_id marked on both layers. The router
does not distinguish it from a track cell. It simply finds that this
position is passable on both layers and uses it as a layer transition
point. Pre-seeding vias is therefore a natural and reliable way to
control where layer transitions happen.


## COMMON ROUTING ERRORS

### Routing between IC pins
The router's pad keep-out radius prevents this, but the AI should
not attempt routes that would require threading between pins. If
a route needs to reach an interior IC pin, approach from the end
of the pin row or use a via to B.Cu.

### Partial waypoint routes leave orphan tracks
When a multi-segment waypoint route partially fails (some segments
route, others don't), the successful segments become orphan tracks
not connected to any pad. After any failed route in a waypoint
sequence, immediately unroute the net to clean up partial segments.

### Forgetting to unroute before re-routing
The route command adds NEW tracks. It does not replace existing
tracks for that net. Always unroute first if changing a route.

### Wrong pad coordinates
The most common cause of failed routes. Always compute pad
positions explicitly using the rotation rules in PCB_FORMAT_DPCB.md.
Do not estimate or round pad positions.

### Routing on the wrong layer for SMD pads
SMD pads only exist on their placement layer. A B.Cu trace cannot
reach an F.Cu SMD pad without a via. The router handles this in
auto mode, but explicit layer commands must account for it.


## DESIGN LOOP

Layout has two phases:
1. **Initial placement** — position all components based on netlist
   connectivity, signal flow, and physical constraints. This happens
   first and is covered in PCB_PLACEMENT.md.
2. **Route-and-adjust loop** — route traces, evaluate results, adjust
   placement or routing as needed. Placement and routing interleave
   freely in this phase.

This section covers phase 2. Once initial placement is done, the AI
shifts focus between components and traces as they interplay.

```
          ┌─────────────┐
          │  EVALUATE    │
          │  the result  │
          └──────┬───────┘
                 │
        ┌────────┴────────┐
        ▼                 ▼
  ┌───────────┐    ┌────────────┐
  │  MOVE     │    │  ROUTE     │
  │  component│    │  trace     │
  └─────┬─────┘    └─────┬──────┘
        │                │
        └────────┬───────┘
                 ▼
          ┌─────────────┐
          │  EVALUATE    │
          └─────────────┘
```

### Evaluate after every action

After each route or move, ask:

- **Route OK, low segment count** → good, continue to next net
- **Route OK, high segment count** → placement problem. Which
  component is in the way? Can it move toward its connections?
- **Route failed** → is a component blocking? Is a routed trace
  blocking? Decide: move component, unroute blocker, or try
  different layer/waypoints
- **Component moved** → do nearby routes need re-routing? Are
  existing routes now longer than necessary?

### Decision: move or route?

Use `pads <net>` to see where a component's pads connect. If a
component is far from its connections, move it closer FIRST.
A 2mm component move always beats a complex routing workaround.

If the route is simply blocked by another trace, that's a routing
problem — try rip-up-and-retry or waypoints. If the route is
fighting the layout itself (hugging, high segments, boxed-in
pads), that's a placement problem.

### Workflow

```
1. Load board:     load board.dpcb
2. Check status:   status
3. List nets:      nets
4. Use pads <net> to check pad positions — do not hand-calculate
5. For each net:
     a. Check: is the component well-placed for this connection?
        - Use pads <net> to see source and destination
        - If component is far from connection, move it first
     b. Route: route <net> <x1,y1> <x2,y2> [layer]
     c. If route has vias: run viacheck immediately
        - FAIL = via too close to pad → unroute, re-place via, re-route
        - Do NOT defer this — a bad via blocks corridors for later nets
     d. Evaluate: segment count, length, path quality
        - High segments for short distance? Reconsider placement
        - Failed? Reconsider placement OR rip-up blocking net
     e. Repeat b-d, shifting between move and route as needed
6. Save result:    save routed_board.dpcb
7. Verify:         python3 check_dpcb.py routed_board.dpcb
8. Fix violations, repeat from step 5
9. When clean, proceed to gen_pcb.py
```