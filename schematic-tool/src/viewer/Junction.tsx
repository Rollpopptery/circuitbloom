import type { Junction } from "../types";
import { JUNCTION_RADIUS } from "./style";

interface Props {
  junction: Junction;
}

export function JunctionView({ junction }: Props) {
  return (
    <circle
      cx={junction.position.x}
      cy={junction.position.y}
      r={JUNCTION_RADIUS}
      fill="currentColor"
    />
  );
}
