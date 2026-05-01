import type { Schematic, Subcircuit } from "../types";
import { useAppStore, PROBE_COLORS } from "../state/store";
import { ComponentView } from "./Component";
import { JunctionView } from "./Junction";
import { NetLabelView } from "./NetLabel";
import { WireView } from "./Wire";

interface Props {
  schematic: Schematic;
  subcircuit: Subcircuit;
}

export function Sheet({ schematic, subcircuit }: Props) {
  const selectedRefDes = useAppStore((s) => s.selectedRefDes);
  const select = useAppStore((s) => s.select);
  const probes = useAppStore((s) => s.probes);

  // Map spiceNode → probe color for quick label lookup
  const probeColorMap = new Map<string, string>();
  probes.forEach((p, i) => {
    if (p) probeColorMap.set(p.spiceNode, PROBE_COLORS[i]);
  });

  // Also map netName → probe color (for labels that use display names)
  const probeNameMap = new Map<string, string>();
  probes.forEach((p, i) => {
    if (p) probeNameMap.set(p.netName, PROBE_COLORS[i]);
  });

  return (
    <g>
      {subcircuit.wires.map((w) => (
        <WireView key={w.id} wire={w} />
      ))}
      {subcircuit.junctions.map((j, i) => (
        <JunctionView key={i} junction={j} />
      ))}
      {subcircuit.labels.map((l, i) => (
        <NetLabelView
          key={i}
          label={l}
          probeColor={probeNameMap.get(l.name) ?? probeColorMap.get(l.name)}
        />
      ))}
      {subcircuit.components.map((c) => (
        <ComponentView
          key={c.refdes}
          instance={c}
          schematic={schematic}
          isSelected={selectedRefDes === c.refdes}
          onSelect={select}
        />
      ))}
      {/* Probe markers */}
      {probes.map((p, i) =>
        p ? (
          <g key={i} transform={`translate(${p.position.x} ${p.position.y})`} style={{ pointerEvents: "none" }}>
            <circle r={0.9} fill={PROBE_COLORS[i]} stroke="#000" strokeWidth={0.08} opacity={0.92} />
            <text
              fontSize={0.9}
              textAnchor="middle"
              dominantBaseline="central"
              fontFamily="sans-serif"
              fontWeight="bold"
              fill="#000"
            >
              {i + 1}
            </text>
          </g>
        ) : null
      )}
    </g>
  );
}
