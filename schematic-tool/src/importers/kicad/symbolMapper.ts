import type { Pin, Point, Symbol, SymbolGraphic } from "../../types";
import type { SExpr } from "./parser";
import { isList, head, findAll, findOne, asNumber, asString, fy } from "./helpers";

export function mapLibSymbol(sym: SExpr[]): Symbol {
  const id = asString(sym[1]);
  const graphics: SymbolGraphic[] = [];
  const pins: Pin[] = [];

  function collectFromUnit(unit: SExpr[]) {
    for (const child of unit) {
      if (!isList(child)) continue;
      const tag = head(child);

      if (tag === "rectangle") {
        const start = findOne(child, "start");
        const end = findOne(child, "end");
        if (start && end) {
          const y1 = fy(asNumber(start[2])), y2 = fy(asNumber(end[2]));
          graphics.push({
            type: "rectangle",
            min: { x: asNumber(start[1]), y: Math.min(y1, y2) },
            max: { x: asNumber(end[1]),   y: Math.max(y1, y2) },
          });
        }
      } else if (tag === "polyline") {
        const pts = findOne(child, "pts");
        if (pts) {
          const points: Point[] = [];
          for (const p of pts) {
            if (isList(p) && head(p) === "xy") {
              points.push({ x: asNumber(p[1]), y: fy(asNumber(p[2])) });
            }
          }
          graphics.push({ type: "polyline", points });
        }
      } else if (tag === "circle") {
        const center = findOne(child, "center");
        const radius = findOne(child, "radius");
        if (center && radius) {
          graphics.push({
            type: "circle",
            center: { x: asNumber(center[1]), y: fy(asNumber(center[2])) },
            radius: asNumber(radius[1]),
          });
        }
      } else if (tag === "arc") {
        const start = findOne(child, "start");
        const mid   = findOne(child, "mid");
        const end   = findOne(child, "end");
        if (start && mid && end) {
          graphics.push({
            type: "polyline",
            points: [
              { x: asNumber(start[1]), y: fy(asNumber(start[2])) },
              { x: asNumber(mid[1]),   y: fy(asNumber(mid[2])) },
              { x: asNumber(end[1]),   y: fy(asNumber(end[2])) },
            ],
          });
        }
      } else if (tag === "pin") {
        const at     = findOne(child, "at");
        const number = findOne(child, "number");
        const name   = findOne(child, "name");
        if (at) {
          pins.push({
            id:       number ? asString(number[1]) : String(pins.length + 1),
            name:     name   ? asString(name[1])   : "",
            position: { x: asNumber(at[1]), y: fy(asNumber(at[2])) },
          });
        }
      } else if (tag === "symbol") {
        collectFromUnit(child);
      }
    }
  }

  collectFromUnit(sym);

  return {
    id,
    name: id.split(":").pop() ?? id,
    pins,
    graphics,
  };
}
