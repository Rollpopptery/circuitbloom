# Circuit Bloom

Browser-based SPICE simulation cockpit for KiCad schematics.

**[Live demo](https://rollpopptery.github.io/circuitbloom/)**

## Features

- Load `.kicad_sch` schematics or saved `.spice-project.json` files
- Interactive schematic viewer with pan/zoom
- Place up to 4 voltage probes by clicking wires
- Per-component SPICE templates and model definitions
- Transient, AC, and DC sweep analysis
- Runs [ngspice](https://ngspice.sourceforge.io/) fully in-browser via WebAssembly — no server required
- Waveform plot with min/max decimation for large datasets
- Save/load projects (File System Access API with download fallback)

## Tech Stack

- React + TypeScript + Vite
- [eecircuit-engine](https://www.npmjs.com/package/eecircuit-engine) — ngspice compiled to WASM
- Zustand for state management

## Running Locally

```bash
npm install
npm run dev
```

## Supported KiCad Symbols

Any KiCad schematic symbol can be simulated by setting a SPICE template and model in the Properties panel. Built-in defaults are provided for R, C, L, D, Q (BJT), M (MOSFET), V, and I.
