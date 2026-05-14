import type { ComponentInstance, Schematic } from "../types";
import type { Netlist, NetlistComponent, Net } from "../types";
import { transformPoint } from "../utils/geometry";

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

function snapKey(p: { x: number; y: number }): string {
  return `${Math.round(p.x * 100) / 100},${Math.round(p.y * 100) / 100}`;
}

function isPower(comp: ComponentInstance): boolean {
  return comp.refdes.startsWith("#PWR") || comp.symbolId.toLowerCase().startsWith("power:");
}

function isGround(name: string): boolean {
  return /^(gnd|0|vss|agnd|dgnd|earth)$/i.test(name.trim());
}

function toSpiceNode(name: string): string {
  if (isGround(name)) return "0";
  return name.replace(/[^a-zA-Z0-9_.]/g, "_");
}

export function extractNetlist(schematic: Schematic): Netlist {
  const sub = schematic.subcircuits[schematic.topLevel];
  if (!sub) return { components: [], nets: [] };

  const uf = new UnionFind();

  for (const wire of sub.wires) {
    for (let i = 0; i < wire.points.length - 1; i++) {
      uf.union(snapKey(wire.points[i]), snapKey(wire.points[i + 1]));
    }
  }

  // Net names from labels
  const rootNames = new Map<string, string>();
  for (const label of sub.labels) {
    const root = uf.find(snapKey(label.position));
    if (!rootNames.has(root)) rootNames.set(root, label.name);
  }

  // Power symbol pins contribute net names
  for (const comp of sub.components) {
    if (!isPower(comp)) continue;
    const sym = schematic.symbols[comp.symbolId];
    if (!sym) continue;
    for (const pin of sym.pins) {
      const world = transformPoint(pin.position, comp.position, comp.rotation, comp.mirror, comp.mirrorX);
      const root = uf.find(snapKey(world));
      if (!rootNames.has(root)) rootNames.set(root, comp.value || comp.refdes);
    }
  }

  let autoIdx = 0;
  function nodeOf(root: string): string {
    if (!rootNames.has(root)) rootNames.set(root, `net${++autoIdx}`);
    return toSpiceNode(rootNames.get(root)!);
  }

  const netlistComponents: NetlistComponent[] = [];
  const netPins = new Map<string, Array<{ refdes: string; pinId: string }>>();

  for (const comp of sub.components) {
    if (isPower(comp)) continue;
    const sym = schematic.symbols[comp.symbolId];
    if (!sym) continue;

    const pinNets: Record<string, string> = {};
    for (const pin of sym.pins) {
      const world = transformPoint(pin.position, comp.position, comp.rotation, comp.mirror, comp.mirrorX);
      const root = uf.find(snapKey(world));
      const node = nodeOf(root);
      pinNets[pin.id] = node;
      if (pin.name && pin.name !== pin.id) pinNets[pin.name] = node;
      if (!netPins.has(node)) netPins.set(node, []);
      netPins.get(node)!.push({ refdes: comp.refdes, pinId: pin.id });
    }

    netlistComponents.push({
      refdes: comp.refdes,
      symbolId: comp.symbolId,
      value: comp.value,
      pinNets,
    });
  }

  const nets: Net[] = [...netPins.entries()].map(([name, pins]) => ({ name, pins }));
  return { components: netlistComponents, nets };
}
