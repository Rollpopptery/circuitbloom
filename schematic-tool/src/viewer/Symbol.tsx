import type { Symbol, SymbolGraphic } from "../types";
import { PIN_DOT_COLOR, PIN_DOT_RADIUS, STROKE_WIDTH } from "./style";

function GraphicView({ g }: { g: SymbolGraphic }) {
  switch (g.type) {
    case "line": {
      const d = g.points
        .map((p, i) => (i === 0 ? "M" : "L") + p.x + " " + p.y)
        .join(" ");
      return (
        <path
          d={d}
          stroke={g.stroke ?? "currentColor"}
          strokeWidth={g.strokeWidth ?? STROKE_WIDTH}
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      );
    }

    case "rectangle":
      return (
        <rect
          x={g.min.x}
          y={g.min.y}
          width={g.max.x - g.min.x}
          height={g.max.y - g.min.y}
          fill={g.fill ?? "white"}
          stroke={g.stroke ?? "currentColor"}
          strokeWidth={g.strokeWidth ?? STROKE_WIDTH}
        />
      );

    case "circle":
      return (
        <circle
          cx={g.center.x}
          cy={g.center.y}
          r={g.radius}
          fill={g.fill ?? "none"}
          stroke={g.stroke ?? "currentColor"}
          strokeWidth={g.strokeWidth ?? STROKE_WIDTH}
        />
      );

    case "arc": {
      const a0 = (g.startAngle * Math.PI) / 180;
      const a1 = (g.endAngle * Math.PI) / 180;
      const x0 = g.center.x + g.radius * Math.cos(a0);
      const y0 = g.center.y + g.radius * Math.sin(a0);
      const x1 = g.center.x + g.radius * Math.cos(a1);
      const y1 = g.center.y + g.radius * Math.sin(a1);
      let sweep = g.endAngle - g.startAngle;
      while (sweep < 0) sweep += 360;
      while (sweep >= 360) sweep -= 360;
      const largeArc = sweep > 180 ? 1 : 0;
      const d = `M ${x0} ${y0} A ${g.radius} ${g.radius} 0 ${largeArc} 1 ${x1} ${y1}`;
      return (
        <path
          d={d}
          stroke={g.stroke ?? "currentColor"}
          strokeWidth={g.strokeWidth ?? STROKE_WIDTH}
          fill="none"
        />
      );
    }

    case "polyline": {
      const d = g.points
        .map((p, i) => (i === 0 ? "M" : "L") + p.x + " " + p.y)
        .join(" ");
      return (
        <path
          d={d + (g.fill ? " Z" : "")}
          stroke={g.stroke ?? "currentColor"}
          strokeWidth={g.strokeWidth ?? STROKE_WIDTH}
          fill={g.fill ?? "none"}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      );
    }

    case "text":
      return (
        <text
          x={g.position.x}
          y={g.position.y}
          fontSize={g.fontSize ?? 6}
          fill={g.fill ?? "currentColor"}
          textAnchor="middle"
          fontFamily="sans-serif"
        >
          {g.content}
        </text>
      );
  }
}

interface Props {
  symbol: Symbol;
}

export function SymbolView({ symbol }: Props) {
  return (
    <g>
      {symbol.graphics.map((g, i) => (
        <GraphicView key={i} g={g} />
      ))}
      {symbol.pins.map((pin) => (
        <g key={pin.id}>
          <circle cx={pin.position.x} cy={pin.position.y} r={PIN_DOT_RADIUS} fill={PIN_DOT_COLOR} />
          <text x={pin.position.x} y={pin.position.y - 0.6} textAnchor="middle" fontSize={0.9} fill="#888" fontFamily="monospace">{pin.id}</text>
        </g>
      ))}
    </g>
  );
}
