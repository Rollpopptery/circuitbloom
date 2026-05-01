import type { NetLabel } from "../types";

interface Props {
  label: NetLabel;
  probeColor?: string;
}

export function NetLabelView({ label, probeColor }: Props) {
  const fontSize = label.fontSize ?? 1.27;

  const transform = label.rotation
    ? `translate(${label.position.x} ${label.position.y}) rotate(${-label.rotation})`
    : `translate(${label.position.x} ${label.position.y})`;

  const anchor = label.justify?.includes("right")
    ? "end"
    : label.justify?.includes("left")
      ? "start"
      : "middle";

  const fill = probeColor ?? (label.isPower ? "#a00" : "#222");

  return (
    <text
      transform={transform}
      fontSize={fontSize}
      textAnchor={anchor}
      dominantBaseline="middle"
      fontFamily="sans-serif"
      fontWeight={probeColor ? "bold" : "normal"}
      fill={fill}
    >
      {label.name}
    </text>
  );
}
