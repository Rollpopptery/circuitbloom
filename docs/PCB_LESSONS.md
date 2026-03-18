# PCB LESSONS LEARNED
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline
#
# Universal design and routing rules learned from real layout attempts.
# See PCB_CHECKER_NOTES.md for checker-specific limitations and known false positives.


## ROUTING RULES

### TH pads exist on ALL copper layers
A trace on B.Cu passing through a TH pad on a different net is a short circuit.
Every trace on every layer must be checked against every TH pad. This is the
primary failure mode — trace-vs-trace crossing checks alone are insufficient.

### TH pads are bilateral — vias co-located with TH pads are redundant
The TH pad barrel connects all layers. A via placed at a TH pad position, or
reachable from a TH pad in a single track segment, is unnecessary. Remove it.
A route that transitions layers only needs a via if NO TH pad on the same net
is available at the transition point.

### DIP pin pitch is too tight for between-pin routing
2.54mm pitch with 1.6mm pads leaves 0.54mm between exclusion zones.
Minimum trace+clearance needs 0.65mm. Between-pin routing is physically
impossible on any layer.

### The IC channel is the critical B.Cu resource
The only safe B.Cu path through a DIP is between the pin columns.
Multiple nets need separate x-lanes (min 0.45mm apart).
A single net spanning the full channel height blocks all other nets.

### Route power AROUND the IC, not through it
VCC/GND traces through the IC channel or across pin columns cross every
other net. Route power on F.Cu around the outside with branches to pins.


## PLACEMENT RULES

### Placement determines routing difficulty
IC-centric placement with passives near their connected pins reduces route
lengths by 50%+ and makes crossing-free routing achievable. Never start
routing from a default column-dump placement.

### Check courtyard overlaps after every placement change
Components placed too close create assembly problems even if electrically
clean. Use courtyard.py check after every placement iteration.


## PROCESS RULES

### Always gen+reload after every check, pass or fail
The human needs to see the board evolve live in KiCad. Rule: after every
check_dpcb.py run (pass or fail), immediately run gen_pcb.py + reload_board.py.
No exceptions.

### Computational verification is not optional
Hundreds of distance calculations cannot be done by inspection. Run the
checkers. Read the output. Fix violations. Re-run. Typically 3-7 iterations
to converge. Skipping produces shorted boards.

### Verify the toolchain before trusting it
A checker giving PASS means nothing if the checker has a bug. After any
change to dpcb_parser.py, verify pad positions from --verbose against KiCad
DRC output for all four rotation cases.

### KiCad 9.0.7 rotation convention (verified)
    r0:   (dx, dy) → ( dx,  dy)
    r90:  (dx, dy) → ( dy, -dx)
    r180: (dx, dy) → (-dx, -dy)
    r270: (dx, dy) → (-dy,  dx)

### gen_pcb.py argument order
base.kicad_pcb FIRST, then design.dpcb. Reversed args produce 0 footprints
and 0 tracks silently.
Always: python3 gen_pcb.py base.kicad_pcb design.dpcb -o output.kicad_pcb

### Granular checks are better than monolithic ones
Each check should do one thing and be independently runnable with --check.
Hints (rotation, kink) must be separated from hard violations so the summary
exit code remains meaningful for automated gating.
