import type { Wire } from "../types";
import { STROKE_WIDTH, WIRE_COLOR } from "./style";

interface Props {
  wire: Wire;
}

export function WireView({ wire }: Props) {
  const d = wire.points
    .map((p, i) => (i === 0 ? "M" : "L") + p.x + " " + p.y)
    .join(" ");
  return (
    <path
      d={d}
      stroke={WIRE_COLOR}
      fill="none"
      strokeWidth={STROKE_WIDTH}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  );
}
