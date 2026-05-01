import type {
  ComponentInstance,
  Junction,
  NetLabel,
  Point,
  Subcircuit,
  Symbol,
  TextItem,
  Wire,
} from "../../types";
import type { SExpr } from "./parser";
import {
  isList, head, findAll, findOne,
  asNumber, asString, rotationFromAngle,
  parseTextEffects, parsePropertyAsText,
} from "./helpers";
import { mapLibSymbol } from "./symbolMapper";

export function mapSchematic(root: SExpr[]): {
  symbols: Record<string, Symbol>;
  subcircuit: Subcircuit;
} {
  const symbols: Record<string, Symbol> = {};

  const libSymbols = findOne(root, "lib_symbols");
  if (libSymbols) {
    for (const sym of findAll(libSymbols, "symbol")) {
      const s = mapLibSymbol(sym);
      symbols[s.id] = s;
    }
  }

  // Component instances
  const components: ComponentInstance[] = [];
  for (const inst of findAll(root, "symbol")) {
    const libIdNode = findOne(inst, "lib_id");
    const at = findOne(inst, "at");
    if (!libIdNode || !at) continue;
    const symbolId = asString(libIdNode[1]);

    const texts: Record<string, TextItem> = {};
    let refdes = "?";
    let value = "";

    for (const child of inst) {
      if (!isList(child) || head(child) !== "property") continue;
      const name = asString(child[1]);
      const text = parsePropertyAsText(child);
      if (!text) continue;
      texts[name.toLowerCase()] = text;
      if (name === "Reference") refdes = text.content;
      else if (name === "Value") value = text.content;
    }

    const mirrorNode = findOne(inst, "mirror");
    const mirrorAxis = mirrorNode && typeof mirrorNode[1] === "string" ? mirrorNode[1] : null;

    components.push({
      refdes,
      symbolId,
      position: { x: asNumber(at[1]), y: asNumber(at[2]) },
      rotation: rotationFromAngle(at[3] !== undefined ? asNumber(at[3]) : 0),
      mirror:  mirrorAxis === "y" || undefined,
      mirrorX: mirrorAxis === "x" || undefined,
      value,
      properties: {},
      texts,
    });
  }

  // Wires
  const wires: Wire[] = [];
  let wireCounter = 0;
  for (const w of findAll(root, "wire")) {
    const pts = findOne(w, "pts");
    if (!pts) continue;
    const points: Point[] = [];
    for (const p of pts) {
      if (isList(p) && head(p) === "xy") {
        points.push({ x: asNumber(p[1]), y: asNumber(p[2]) });
      }
    }
    wires.push({ id: `w${wireCounter++}`, points });
  }

  // Junctions
  const junctions: Junction[] = [];
  for (const j of findAll(root, "junction")) {
    const at = findOne(j, "at");
    if (at) {
      junctions.push({ position: { x: asNumber(at[1]), y: asNumber(at[2]) } });
    }
  }

  // Labels (local, global, hierarchical)
  const labels: NetLabel[] = [];
  for (const tag of ["label", "global_label", "hierarchical_label"]) {
    for (const lbl of findAll(root, tag)) {
      const at = findOne(lbl, "at");
      const name = typeof lbl[1] === "string" ? lbl[1] : "";
      if (!at || !name) continue;
      const { fontSize, justify } = parseTextEffects(lbl);
      labels.push({
        position: { x: asNumber(at[1]), y: asNumber(at[2]) },
        name,
        isPower: tag === "global_label",
        rotation: at[3] !== undefined ? asNumber(at[3]) : 0,
        fontSize,
        justify,
      });
    }
  }

  const subcircuit: Subcircuit = {
    id: "top",
    name: "Top",
    ports: [],
    components,
    wires,
    junctions,
    labels,
  };

  return { symbols, subcircuit };
}
