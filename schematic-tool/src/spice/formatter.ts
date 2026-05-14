import type { Netlist } from "../types";
import type { ComponentParams } from "../state/store";
import { defaultTemplate } from "../utils/spice";

export type AnalysisConfig =
  | { type: "tran"; step: string; stop: string; tstart?: string; tmax?: string }
  | { type: "ac"; variation: "dec" | "oct" | "lin"; points: number; fstart: string; fstop: string }
  | { type: "dc"; source: string; start: string; stop: string; step: string };

function analysisLine(a: AnalysisConfig): string {
  switch (a.type) {
    case "tran": {
      const tstart = a.tstart?.trim() || "0";
      const tmax = a.tmax?.trim();
      return tmax
        ? `.tran ${a.step} ${a.stop} ${tstart} ${tmax}`
        : `.tran ${a.step} ${a.stop}`;
    }
    case "ac":   return `.ac ${a.variation} ${a.points} ${a.fstart} ${a.fstop}`;
    case "dc":   return `.dc ${a.source} ${a.start} ${a.stop} ${a.step}`;
  }
}

function resolveTemplate(
  template: string,
  refdes: string,
  pinNets: Record<string, string>
): string {
  return template
    .replace(/\{refdes\}/g, refdes)
    .replace(/\{([^{}]+)\}/g, (_, n) => pinNets[n] ?? `_NC${n}`);
}

export function formatSpice(
  netlist: Netlist,
  componentParams: Record<string, ComponentParams>,
  analysis: AnalysisConfig,
  title = "Schematic",
  saveNets: string[] = []
): string {
  const lines: string[] = [`* ${title}`, ""];

  for (const comp of netlist.components) {
    const template =
      componentParams[comp.refdes]?.spiceTemplate ?? defaultTemplate(comp.refdes, comp.value);
    lines.push(resolveTemplate(template, comp.refdes, comp.pinNets));
  }

  // Inject built-in generic models when referenced in any component line
  // but not already covered by an explicit per-component model definition
  const BUILTIN_MODELS: Record<string, string> = {
    PMOS: ".model PMOS PMOS (level=1 vto=-2 kp=10u lambda=0.01)",
    NMOS: ".model NMOS NMOS (level=1 vto=2  kp=120u lambda=0.01)",
  };

  // Collect explicit model blocks (may themselves define PMOS/NMOS with params)
  const models = netlist.components
    .map((c) => componentParams[c.refdes]?.model?.trim())
    .filter(Boolean) as string[];

  const allModelText = models.join("\n").toUpperCase();
  const builtins: string[] = [];
  for (const [name, def] of Object.entries(BUILTIN_MODELS)) {
    const used = lines.some(l => new RegExp(`\\b${name}\\b`, "i").test(l));
    const alreadyDefined = new RegExp(`\\.MODEL\\s+${name}\\b`, "i").test(allModelText);
    if (used && !alreadyDefined) builtins.push(def);
  }

  if (models.length > 0 || builtins.length > 0) {
    lines.push("", "* Models", ...builtins, ...models);
  }

  // Element name is the first token of the resolved SPICE line (e.g. LL1, not L1)
  const currentSaves = netlist.components
    .filter(c => componentParams[c.refdes]?.probeI)
    .map(c => {
      const template = componentParams[c.refdes]?.spiceTemplate ?? defaultTemplate(c.refdes, c.value);
      const resolved = resolveTemplate(template, c.refdes, c.pinNets);
      const elementName = resolved.trim().split(/\s+/)[0].toLowerCase();
      return `i(${elementName})`;
    });

  if (saveNets.length > 0 || currentSaves.length > 0) {
    const voltages = saveNets.map(n => `v(${n})`);
    lines.push("", `.save ${[...voltages, ...currentSaves].join(" ")}`);
  }

  lines.push("", analysisLine(analysis), ".end");
  return lines.join("\n");
}
