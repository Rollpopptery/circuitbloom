import { useRef } from "react";
import { useAppStore } from "../state/store";
import { Sheet } from "./Sheet";
import { usePanZoom } from "./usePanZoom";
import { findNetAtPoint } from "../netlist/probe";

const HIT_PX = 8;

export function SchematicView() {
  const schematic = useAppStore((s) => s.schematic);
  const currentId = useAppStore((s) => s.currentSubcircuitId);
  const select = useAppStore((s) => s.select);
  const addProbe = useAppStore((s) => s.addProbe);
  const svgRef = useRef<SVGSVGElement>(null);
  const { viewport, isDragging } = usePanZoom(svgRef);

  const subcircuit =
    schematic && currentId ? schematic.subcircuits[currentId] : null;

  const transform = `translate(${viewport.pan.x} ${viewport.pan.y}) scale(${viewport.zoom})`;

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    if (isDragging) return;
    if (!schematic || !svgRef.current) { select(null); return; }
    const rect = svgRef.current.getBoundingClientRect();
    const schX = (e.clientX - rect.left - viewport.pan.x) / viewport.zoom;
    const schY = (e.clientY - rect.top - viewport.pan.y) / viewport.zoom;
    const tol = HIT_PX / viewport.zoom;
    const probe = findNetAtPoint(schematic, { x: schX, y: schY }, tol);
    if (probe) {
      addProbe(probe);
    } else {
      select(null);
    }
  }

  return (
    <svg
      ref={svgRef}
      width="100%"
      height="100%"
      onClick={handleClick}
      style={{
        background: "#fafafa",
        color: "#222",
        display: "block",
        cursor: isDragging ? "grabbing" : "crosshair",
      }}
    >
      <defs>
        <pattern
          id="grid"
          width={50}
          height={50}
          patternUnits="userSpaceOnUse"
          patternTransform={transform}
        >
          <circle cx={0} cy={0} r={1} fill="#ccc" />
        </pattern>
        <filter id="selection-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feDropShadow dx="0" dy="0" stdDeviation="1.5" floodColor="#2196F3" floodOpacity="0.85" />
        </filter>
      </defs>
      <rect width="100%" height="100%" fill="url(#grid)" />

      {subcircuit ? (
        <g transform={transform}>
          <Sheet schematic={schematic!} subcircuit={subcircuit} />
        </g>
      ) : (
        <text x={20} y={40} fontSize={14} fill="#666">
          No schematic loaded.
        </text>
      )}
    </svg>
  );
}
