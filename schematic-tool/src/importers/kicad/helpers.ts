import type { Justify, TextItem } from "../../types";
import type { SExpr } from "./parser";

export function isList(x: SExpr): x is SExpr[] {
  return Array.isArray(x);
}

export function head(x: SExpr): string | null {
  if (isList(x) && typeof x[0] === "string") return x[0];
  return null;
}

export function findAll(list: SExpr[], tag: string): SExpr[][] {
  return list.filter((x): x is SExpr[] => isList(x) && head(x) === tag);
}

export function findOne(list: SExpr[], tag: string): SExpr[] | null {
  return findAll(list, tag)[0] ?? null;
}

export function asNumber(x: SExpr | undefined): number {
  if (typeof x === "number") return x;
  if (typeof x === "string") {
    const n = Number(x);
    if (!isNaN(n)) return n;
  }
  throw new Error(`Expected number, got ${JSON.stringify(x)}`);
}

export function asString(x: SExpr | undefined): string {
  if (typeof x === "string") return x;
  throw new Error(`Expected string, got ${JSON.stringify(x)}`);
}

export function rotationFromAngle(angle: number): 0 | 90 | 180 | 270 {
  const a = ((Math.round(angle) % 360) + 360) % 360;
  if (a === 0 || a === 90 || a === 180 || a === 270) return a as 0 | 90 | 180 | 270;
  return 0;
}

// KiCad lib_symbols use Y-up; SVG is Y-down. Negate Y on import.
export function fy(y: number): number { return -y; }

export function parseTextEffects(item: SExpr[]): {
  fontSize: number;
  justify?: Justify;
  hidden: boolean;
} {
  let fontSize = 1.27;
  let justify: Justify | undefined;
  let hidden = false;

  const effects = findOne(item, "effects");
  if (effects) {
    const font = findOne(effects, "font");
    if (font) {
      const size = findOne(font, "size");
      if (size) fontSize = asNumber(size[1]);
    }
    const just = findOne(effects, "justify");
    if (just) {
      const tokens = just.slice(1).filter((x): x is string => typeof x === "string");
      justify = tokens.join(" ") as Justify;
    }
    for (const child of effects) {
      if (isList(child) && head(child) === "hide") hidden = true;
    }
  }

  return { fontSize, justify, hidden };
}

export function parsePropertyAsText(prop: SExpr[]): TextItem | null {
  const value = asString(prop[2]);
  const at = findOne(prop, "at");
  if (!at) return null;

  const { fontSize, justify, hidden } = parseTextEffects(prop);

  return {
    content: value,
    position: { x: asNumber(at[1]), y: asNumber(at[2]) },
    rotation: at[3] !== undefined ? asNumber(at[3]) : 0,
    fontSize,
    justify,
    hidden,
  };
}
