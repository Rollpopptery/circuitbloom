# CLAUDE.md
# AI-Driven KiCad PCB Design Pipeline
# Human: <redacted>
# Last updated: 2026-03-09


## READ FIRST

Before any PCB work, read the relevant docs from utilities/docs/.

For any PCB layout task, always read at minimum:
- utilities/docs/PCB_PIPELINE.md       — workflow, tools, verified loop
- utilities/docs/PCB_FORMAT_DPCB.md    — .dpcb format specification
- utilities/docs/PCB_VERIFICATION.md   — verification checks
- utilities/docs/PCB_LESSONS.md        — universal rules (always read)
- utilities/docs/PCB_CHECKER_NOTES.md  — checker limitations and false positives

For placement work, also read:
- utilities/docs/PCB_PLACEMENT.md

For routing work, also read:
- utilities/docs/PCB_ROUTING.md
- utilities/docs/PCB_KEEPOUTS.md

For design rule questions:
- utilities/docs/PCB_DESIGN_RULES.md


## PROJECT STRUCTURE

```
~/projects/pcb_design/          (= /workspace inside Docker)
    CLAUDE.md                   — this file
    Dockerfile                  — container definition
    docker-compose.yml          — container configuration
    utilities/
        distill_pcb.py          — .kicad_pcb → .dpcb
        gen_pcb.py              — .dpcb + base .kicad_pcb → .kicad_pcb
        check_dpcb.py           — verify .dpcb before generating
        reload_board.py         — tell KiCad to reload live via IPC API
        distill_sch.py          — .kicad_sch → .dsch
        gen_sch.py              — .dsch → .kicad_sch
        kicad_pin_atlas.json    — pin offsets for all KiCad symbols
        checks/                 — check modules used by check_dpcb.py
        docs/                   — all documentation (see READ FIRST)
```


## ENVIRONMENT

Running inside Docker container:
- /workspace   = ~/projects/pcb_design/ on host (mounted)
- /tmp/kicad   = host KiCad IPC socket (mounted)

KiCad 9 runs on the host. Human watches it live.
See PCB_PIPELINE.md for full environment and workflow details.