import type { Schematic } from "../../types";
import { parseSExpression } from "./parser";
import { isList, head } from "./helpers";
import { mapSchematic } from "./schematicMapper";

export function importKicad(text: string): Schematic {
  const parsed = parseSExpression(text);
  if (!isList(parsed) || head(parsed) !== "kicad_sch") {
    throw new Error("Not a KiCad schematic file (expected (kicad_sch ...))");
  }
  const { symbols, subcircuit } = mapSchematic(parsed);

  return {
    version: "0.1",
    symbols,
    subcircuits: { [subcircuit.id]: subcircuit },
    topLevel: subcircuit.id,
  };
}
