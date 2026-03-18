# PCB PIPELINE
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline


## PHILOSOPHY

PCB layout is not an optimisation problem to be solved globally.
It is an iterative improvement process using local reasoning,
validated by automated checks at every step.

The AI cannot visually inspect a board. It works numerically —
calculating pad positions, trace-to-pad distances, and crossing
geometry. Every proposed change must be verified computationally
before generating a .kicad_pcb file. The human reviews visually
in KiCad; the AI reviews by running checker scripts.

The goal is not an optimal layout. The goal is a DRC-clean layout
that is manufacturable. Stop when it's clean, not when it's
mathematically perfect.


## TOOL CHAIN

```
.kicad_pcb (from KiCad)
    → distill_pcb.py → .dpcb (compact, AI-readable)
    → AI proposes changes → new .dpcb
    → check_dpcb.py → verify (pad conflicts, crossings, collinear,
                               connectivity, dangling)
    → gen_pcb.py (base .kicad_pcb + .dpcb) → new .kicad_pcb
    → reload_board.py → KiCad reloads live (human sees instantly)
    → kicad-cli pcb drc → DRC report
    → human reviews in KiCad
    → iterate
```


## TWO-WAY SYNCHRONISATION

The pipeline is fully bidirectional:

```
AI → KiCad:
  .dpcb → gen_pcb.py → .kicad_pcb → reload_board.py → live update

KiCad → AI:
  human edits in KiCad → Ctrl+S → distill_pcb.py → .dpcb → AI sees current state
```

The .dpcb is the AI's working format. The .kicad_pcb is KiCad's format.
distill_pcb.py and gen_pcb.py are the bridges between them.


## TOOLS

### distill_pcb.py
Reads verbose .kicad_pcb, extracts layout-relevant info only:
- Footprint references, libraries, positions, rotations
- Pad offsets per footprint type (deduplicated by library)
- Net definitions with pad assignments
- Existing tracks, vias, zones
- Board outline (from Edge.Cuts)

Discards: silkscreen graphics, courtyard, 3D models, UUIDs,
fab layer content, plot parameters, pad geometry details.

```bash
python3 distill_pcb.py input.kicad_pcb -o output.dpcb
```

### check_dpcb.py
Standalone verification tool. Reads a .dpcb and runs all checks
in the checks/ package. Must be run BEFORE gen_pcb.py.

```bash
python3 check_dpcb.py design.dpcb              # run all checks
python3 check_dpcb.py design.dpcb --check pads
python3 check_dpcb.py design.dpcb --check crossings
python3 check_dpcb.py design.dpcb --check collinear
python3 check_dpcb.py design.dpcb --check connectivity
python3 check_dpcb.py design.dpcb --verbose
```

Exit codes: 0 = clean, 1 = violations found.

Checks package (utilities/checks/):
- pad_conflicts.py   — trace-vs-pad distance (critical)
- crossings.py       — same-layer trace crossings
- collinear.py       — collinear conflicts (invisible shorts)
- connectivity.py    — net connectivity
- dangling.py        — dangling segment endpoints
- courtyard.py       — component body overlap (approximate bounding box)
- orphaned_vias.py   — vias with no tracks on one or both layers
- unnecessary_vias.py— vias adjacent (single hop) to TH pads on same net
- via_th_bypass.py   — vias unnecessary due to TH pads within N hops
- dpcb_parser.py     — shared parser (fps, pads, nets, tracks, vias)
- mitre.py           — mitre/chamfer geometry helper

Hint checks (non-violations, printed after summary):
- rotation_hint.py   — suggests component rotation to reduce wirelength
- kink.py            — detects U-turns and same-direction jogs in routing

### gen_pcb.py
Patches an existing .kicad_pcb with layout changes from a .dpcb.
The base .kicad_pcb provides all verbose footprint geometry.
The .dpcb provides the layout intent (positions, tracks, vias).

This is a PATCH operation, not a full generation. Footprint
geometry is always correct because it came from KiCad.

```bash
python3 gen_pcb.py base.kicad_pcb design.dpcb -o output.kicad_pcb
```

### reload_board.py
Tells a running KiCad instance to reload the current board from disk.
Run immediately after gen_pcb.py — KiCad updates live.

```bash
python3 reload_board.py
```

Standard verify-generate-reload cycle:
```bash
python3 check_dpcb.py design.dpcb && \
python3 gen_pcb.py base.kicad_pcb design.dpcb -o output.kicad_pcb && \
python3 reload_board.py
```

### distill_sch.py
Reads verbose .kicad_sch, extracts compact .dsch format for AI use.

### gen_sch.py
Expands .dsch to valid .kicad_sch with embedded lib_symbols.


## ITERATION NUMBERING

```
design_000.kicad_pcb  — original from KiCad
design_001.kicad_pcb  — first layout iteration
design_002.kicad_pcb  — second iteration
```

Every .kicad_pcb has a corresponding .dpcb. The .kicad_pcb is
the base for the next iteration.


## THE VERIFIED LOOP

```
1. Propose placement or routing change in .dpcb
2. Calculate absolute pad positions from FP + PADS lines
3. Run check_dpcb.py — all checks must pass
4. If violations: fix and go to step 3
5. If clean: run gen_pcb.py to produce .kicad_pcb
6. Run reload_board.py — human sees update live in KiCad
7. Human runs DRC
8. If DRC violations: update .dpcb, go to step 2
9. Done when DRC clean and human approves
```


## KICAD IPC API

KiCad 9 exposes a live API via Unix socket at /tmp/kicad/api.sock.
This allows external scripts to control a running KiCad instance.

### Setup (one-time)
Enable in KiCad: Preferences → Plugins → Enable IPC API
Restart KiCad, then verify:
```bash
ls /tmp/kicad/api.sock
python3 -c "from kipy import KiCad; print(KiCad().get_version())"
```

kicad-python is pre-installed in the Docker container.

### Key Board Operations
```python
from kipy import KiCad
kicad = KiCad()
board = kicad.get_board()

board.revert()       # reload from disk — the core live-update operation
board.save()         # save current state to disk
board.get_footprints()
board.get_tracks()
board.get_nets()
board.create_items()
board.update_items()
board.remove_items()
board.begin_commit() / push_commit() / drop_commit()
```


## DOCKER ENVIRONMENT

The AI runs inside a Docker container with full access to:
- /workspace  — mounted from ~/projects/pcb_design/ on the host
- /tmp/kicad  — mounted from host, provides KiCad IPC socket

```bash
cd ~/projects/pcb_design
sudo docker-compose up -d
sudo docker-compose exec claude-agent /bin/bash
```

KiCad runs on the host. The container talks to it via the socket.
The human watches KiCad update live on the host display.


## DRC AND EXPORT COMMANDS

```bash
# Run DRC
kicad-cli pcb drc output.kicad_pcb -o drc.json --format json

# Export SVG for review
kicad-cli pcb export svg output.kicad_pcb -o output.svg
```