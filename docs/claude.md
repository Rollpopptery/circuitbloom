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

For API command reference:
- utilities/docs/PCB_API.md              — all viewer TCP commands

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
        cmd.py                  — CLI helper: routing commands with routing diagnostics flash
        cmd_component.py        — CLI helper: placement commands with placement diagnostics flash
        script_autoplace_force.py — force-directed auto-placement (force + component_repulsion)
        checks/                 — check modules used by check_dpcb.py
        router/component_repulsion.py — physical component spacing (Coulomb-like pairwise repulsion)
        docs/                   — all documentation (see READ FIRST)
```

