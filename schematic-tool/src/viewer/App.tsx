import { useEffect } from "react";
import { useAppStore } from "./state/store";
import type { Schematic } from "./types";
import { SchematicView } from "./viewer/SchematicView";

// Temporary demo data so the viewer has something to draw.
const demoSchematic: Schematic = {
  version: "0.1",
  symbols: {
    "Device:R": {
      id: "Device:R",
      name: "R",
      pins: [
        { id: "1", name: "1", position: { x: 0, y: -20 } },
        { id: "2", name: "2", position: { x: 0, y: 20 } },
      ],
      graphics: [],
    },
  },
  subcircuits: {
    top: {
      id: "top",
      name: "Top",
      ports: [],
      components: [
        {
          refdes: "R1",
          symbolId: "Device:R",
          position: { x: 100, y: 100 },
          rotation: 0,
          value: "10k",
          properties: {},
        },
        {
          refdes: "R2",
          symbolId: "Device:R",
          position: { x: 200, y: 100 },
          rotation: 0,
          value: "4k7",
          properties: {},
        },
      ],
      wires: [
        {
          id: "w1",
          points: [
            { x: 100, y: 120 },
            { x: 200, y: 120 },
          ],
        },
      ],
      junctions: [],
      labels: [{ position: { x: 100, y: 60 }, name: "VCC", isPower: true }],
    },
  },
  topLevel: "top",
};

export default function App() {
  const setSchematic = useAppStore((s) => s.setSchematic);

  useEffect(() => {
    setSchematic(demoSchematic);
  }, [setSchematic]);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <SchematicView />
    </div>
  );
}
