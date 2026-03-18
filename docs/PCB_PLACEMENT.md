# PCB PLACEMENT
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline


## IC-CENTRIC PLACEMENT

1. Place the IC first. It is the anchor.
2. Identify which passive connects to which IC pin.
3. Place each passive near its connected pin(s).
4. Decoupling caps go as close to power pins as possible.
5. Components forming a series chain (e.g. R1→R2 in a voltage
   divider) should be placed so their shared-net pads are adjacent.


## DIP PACKAGE EXCLUSION ZONES

A DIP-8 with 2.54mm pin pitch and 1.6mm TH pads creates
tight exclusion zones:

```
Pin pitch:     2.54mm
Pad diameter:  1.6mm
Clearance:     0.2mm
Exclusion:     pad_radius + clearance = 1.0mm per pad

Gap between adjacent pad exclusion zones:
  2.54 - 2×1.0 = 0.54mm

Minimum trace + clearance: 0.25 + 2×0.2 = 0.65mm
```

CONCLUSION: You CANNOT route a foreign-net trace between
adjacent DIP pins on any layer. The gap (0.54mm) is smaller
than the minimum trace+clearance (0.65mm).


## IC PIN APPROACH RULES

Each IC pin can only be reached from directions that don't
pass through other pins' exclusion zones.

For a DIP-8 (left pins at x=X, right pins at x=X+7.62):
- Left column pins: approach from LEFT (x < X-1) or from
  ABOVE/BELOW (outside the pin y-range)
- Right column pins: approach from RIGHT (x > X+7.62+1)
  or from ABOVE/BELOW
- IC CHANNEL: the space between pin columns (x = X+1 to
  X+6.62) has no pads. Traces can run VERTICALLY through
  this channel on B.Cu.


## IC CHANNEL ROUTING

The channel between DIP pin columns is the only safe path
for B.Cu traces crossing the IC body.

Rules:
- Each net gets its own x-coordinate in the channel (own "lane")
- Lanes should be at least 0.45mm apart (trace + clearance)
- Enter the channel from ABOVE or BELOW the pin y-range
- Do not run long verticals through the full channel height —
  they block horizontal access for other nets
- A net spanning the full channel height will cross every other
  net's horizontal entry. Route it outside the IC instead.

Example lane assignment for a DIP-8 at origin x=140:
```
x=142:   GND lane
x=143.5: DISCH lane
x=145:   THRES lane
```


## PLACEMENT HEURISTICS

### 1. PROXIMITY PRINCIPLE
Connected components should be close together. For each net,
minimise the total distance between the pads on that net.

### 2. SCHEMATIC GROUPING
Components that form a functional subcircuit should be grouped.
For the 555 oscillator:
- Timing: R1, R2, C1 near pins 2/6/7
- Decoupling: C2 near pins 1/8 (VCC/GND)
- Control filter: C3 near pin 5
- Output: R3 near pin 3

### 3. DECOUPLING CAPS FIRST
After the IC, place decoupling capacitors immediately. They
must be as close as possible to the power pins. Hard rule.

### 4. SIGNAL FLOW DIRECTION
Orient the board so signal flow is consistent — left-to-right
or top-to-bottom.

### 5. POWER ROUTING AWARENESS
Leave corridors for power traces. Don't block the natural path
from power input to IC power pins with signal components.

### 6. EDGE COMPONENTS
Connectors, switches, LEDs go on the board edge.

### 7. SWAP TO UNCROSS
If two nets cross due to placement, swap component positions
to eliminate the crossing. Often the most effective improvement.
