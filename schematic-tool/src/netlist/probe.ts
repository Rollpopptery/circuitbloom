import type { Schematic, Point } from "../types";
import type { Probe } from "../state/store";
import { transformPoint } from "../utils/geometry";
import { extractNetlist } from "./extract";

class UnionFind {
  private parent = new Map<string, string>();
  find(x: string): string {
    if (!this.parent.has(x)) this.parent.set(x, x);
    const p = this.parent.get(x)!;
    if (p !== x) this.parent.set(x, this.find(p));
    return this.parent.get(x)!;
  }
  union(a: string, b: string): void {
    const ra = this.find(a), rb = this.find(b);
    if (ra !== rb) this.parent.set(ra, rb);
  }
}

function snapKey(p: Point): string {
  return `${Math.round(p.x * 100) / 100},${Math.round(p.y * 100) / 100}`;
}

function ptSegDist(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function ptOnSeg(p: Point, a: Point, b: Point): Point {
  const dx = b.x - a.x, dy = b.y - a.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { ...a };
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
  return { x: a.x + t * dx, y: a.y + t * dy };
}

function isPower(refdes: string, symbolId: string): boolean {
  return refdes.startsWith("#PWR") || symbolId.toLowerCase().startsWith("power:");
}

export function findNetAtPoint(schematic: Schematic, click: Point, tolerance: number): Probe | null {
  const sub = schematic.subcircuits[schematic.topLevel];
  if (!sub) return null;

  // Find nearest wire segment within tolerance
  let bestDist = tolerance;
  let bestSeg: { a: Point; b: Point } | null = null;
  for (const wire of sub.wires) {
    for (let i = 0; i < wire.points.length - 1; i++) {
      const a = wire.points[i], b = wire.points[i + 1];
      const d = ptSegDist(click, a, b);
      if (d < bestDist) { bestDist = d; bestSeg = { a, b }; }
    }
  }
  if (!bestSeg) return null;

  // Build UnionFind from wires
  const uf = new UnionFind();
  for (const wire of sub.wires) {
    for (let i = 0; i < wire.points.length - 1; i++) {
      uf.union(snapKey(wire.points[i]), snapKey(wire.points[i + 1]));
    }
  }

  const clickRoot = uf.find(snapKey(bestSeg.a));

  // Use extractNetlist's computed names as source of truth
  const netlist = extractNetlist(schematic);

  // Map UF root → spice node name via component pins
  const rootToSpice = new Map<string, string>();
  for (const comp of sub.components) {
    if (isPower(comp.refdes, comp.symbolId)) continue;
    const sym = schematic.symbols[comp.symbolId];
    if (!sym) continue;
    const nlComp = netlist.components.find(c => c.refdes === comp.refdes);
    if (!nlComp) continue;
    for (const pin of sym.pins) {
      const world = transformPoint(pin.position, comp.position, comp.rotation, comp.mirror, comp.mirrorX);
      const root = uf.find(snapKey(world));
      const node = nlComp.pinNets[pin.id];
      if (node) rootToSpice.set(root, node);
    }
  }

  const spiceNode = rootToSpice.get(clickRoot);
  if (!spiceNode) return null;

  // Display name: prefer original label; fall back to spiceNode, GND for "0"
  let netName = spiceNode === "0" ? "GND" : spiceNode;
  for (const label of sub.labels) {
    if (uf.find(snapKey(label.position)) === clickRoot) {
      netName = label.name;
      break;
    }
  }

  return {
    netName,
    spiceNode,
    position: ptOnSeg(click, bestSeg.a, bestSeg.b),
  };
}
