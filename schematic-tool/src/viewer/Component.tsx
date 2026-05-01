import type { ComponentInstance, Justify, Schematic, TextItem } from "../types";
import { SymbolView } from "./Symbol";
import { TEXT_COLOR, TEXT_COLOR_SECONDARY } from "./style";

interface Props {
  instance: ComponentInstance;
  schematic: Schematic;
  isSelected: boolean;
  onSelect: (refdes: string) => void;
}

function justifyToAnchor(j?: Justify): "start" | "middle" | "end" {
  if (!j) return "middle";
  if (j.includes("left")) return "start";
  if (j.includes("right")) return "end";
  return "middle";
}

function justifyToBaseline(j?: Justify): "auto" | "middle" | "hanging" {
  if (!j) return "middle";
  if (j.includes("top")) return "hanging";
  if (j.includes("bottom")) return "auto";
  return "middle";
}

function TextItemView({ item, fill }: { item: TextItem; fill: string }) {
  if (item.hidden) return null;
  const transform =
    item.rotation !== 0
      ? `translate(${item.position.x} ${item.position.y}) rotate(${-item.rotation})`
      : `translate(${item.position.x} ${item.position.y})`;

  return (
    <text
      transform={transform}
      fontSize={item.fontSize}
      textAnchor={justifyToAnchor(item.justify)}
      dominantBaseline={justifyToBaseline(item.justify)}
      fontFamily="sans-serif"
      fill={fill}
    >
      {item.content}
    </text>
  );
}

export function ComponentView({ instance, schematic, isSelected, onSelect }: Props) {
  const symbol = schematic.symbols[instance.symbolId];
  if (!symbol) return null;

  const mirrorScale = instance.mirror && instance.mirrorX ? "scale(-1,-1)"
    : instance.mirror  ? "scale(-1,1)"
    : instance.mirrorX ? "scale(1,-1)"
    : "";
  const bodyTransform = `translate(${instance.position.x} ${instance.position.y}) rotate(${-instance.rotation})${mirrorScale ? " " + mirrorScale : ""}`;
  const bbox = symbol.bbox;
  const refText = instance.texts?.reference;
  const valText = instance.texts?.value;
  const refColor = isSelected ? "#1976D2" : TEXT_COLOR;

  return (
    <g
      data-refdes={instance.refdes}
      onClick={(e) => { e.stopPropagation(); onSelect(instance.refdes); }}
      style={{ cursor: "pointer" }}
    >
      <g transform={bodyTransform} filter={isSelected ? "url(#selection-glow)" : undefined}>
        {/* transparent hit rect so clicks land even in whitespace around the symbol */}
        {bbox ? (
          <rect
            x={bbox.min.x}
            y={bbox.min.y}
            width={bbox.max.x - bbox.min.x}
            height={bbox.max.y - bbox.min.y}
            fill="transparent"
            stroke="none"
          />
        ) : (
          <rect x={-5} y={-5} width={10} height={10} fill="transparent" stroke="none" />
        )}
        <SymbolView symbol={symbol} />
      </g>

      {refText ? (
        <TextItemView item={refText} fill={refColor} />
      ) : (
        <text
          x={instance.position.x + 5}
          y={instance.position.y - 5}
          fontSize={1.5}
          fill={refColor}
          fontFamily="sans-serif"
        >
          {instance.refdes}
        </text>
      )}

      {valText ? (
        <TextItemView item={valText} fill={TEXT_COLOR_SECONDARY} />
      ) : (
        <text
          x={instance.position.x + 5}
          y={instance.position.y + 5}
          fontSize={1.5}
          fill={TEXT_COLOR_SECONDARY}
          fontFamily="sans-serif"
        >
          {instance.value}
        </text>
      )}
    </g>
  );
}
