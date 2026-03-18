# PCB DESIGN RULES
# Last updated: 2026-03-09
# Project: AI-Driven KiCad PCB Design Pipeline


## DEFAULT DESIGN RULES

```
Board thickness:    1.6mm
Copper weight:      1oz (35µm)
Min track width:    0.25mm (10mil)
Min clearance:      0.2mm (8mil)
Min via drill:      0.3mm
Min via annular:    0.15mm (total via diameter 0.6mm)
TH pad diameter:    1.6mm (typical for 0.8mm drill)
TH pad exclusion:   1.0mm radius (pad 0.8mm + clearance 0.2mm)
Power track width:  0.5mm minimum, wider for high current
Edge clearance:     0.3mm from board edge to any copper
```


## NOT YET COVERED

- SMD component routing (different pad exclusion rules)
- Ground plane strategy (B.Cu copper pour)
- High-speed design rules (impedance, length matching)
- Multi-layer stackups (4+ layers)
- Differential pair routing
- Manufacturing output (Gerber, drill files)
