import { Simulation } from "eecircuit-engine";

export interface SimResult {
  variableNames: string[];
  // data[i] = values array for variableNames[i], magnitudes for complex (AC)
  data: number[][];
  dataType: "real" | "complex";
  numPoints: number;
  log: string;
}

let sim: Simulation | null = null;

export async function runSimulation(netlistText: string): Promise<SimResult> {
  if (!sim) sim = new Simulation();
  sim.setNetList(netlistText);
  let raw: Awaited<ReturnType<Simulation["runSim"]>>;
  try {
    raw = await sim.runSim();
  } catch (e) {
    sim = null;   // corrupted state — next run starts fresh
    throw e;
  }
  const errors = sim.getError();
  const fatal = Array.isArray(errors)
    ? errors.filter((e: string) => /error|aborted|not parsed/i.test(e))
    : (typeof errors === "string" && /error|aborted|not parsed/i.test(errors) ? [errors] : []);
  if (fatal.length > 0) {
    sim = null;
    throw new Error(fatal.join("\n"));
  }

  let data: number[][];
  if (raw.dataType === "real") {
    data = raw.data.map((d) => d.values as number[]);
  } else {
    // AC: return magnitude of each complex value
    data = raw.data.map((d) =>
      d.values.map((c) => Math.sqrt(c.real ** 2 + c.img ** 2))
    );
  }

  return {
    variableNames: raw.variableNames,
    data,
    dataType: raw.dataType,
    numPoints: raw.numPoints,
    log: sim.getInfo(),
  };
}
