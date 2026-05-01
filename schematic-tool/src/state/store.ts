import { create } from "zustand";
import type { Schematic, Point } from "../types";
import type { AnalysisConfig } from "../spice/formatter";

export const PROBE_COLORS = ["#FFCC00", "#00CCFF", "#FF66FF", "#66FF00"] as const;

export interface Probe {
  netName: string;   // display name (label name or auto name)
  spiceNode: string; // SPICE-safe node name used in .save
  position: Point;   // schematic coords for marker
}

export interface ComponentParams {
  spiceTemplate: string;
  model: string;   // .model or .subckt block pasted from vendor/datasheet
  notes: string;
  probeI?: boolean;
}

interface ViewportState {
  pan: { x: number; y: number };
  zoom: number;
}

interface AppState {
  schematic: Schematic | null;
  selectedRefDes: string | null;
  currentSubcircuitId: string | null;
  viewport: ViewportState;
  componentParams: Record<string, ComponentParams>;
  analysisConfig: AnalysisConfig;
  probes: (Probe | null)[];

  setSchematic: (s: Schematic) => void;
  loadProject: (
    schematic: Schematic,
    componentParams: Record<string, ComponentParams>,
    analysisConfig?: AnalysisConfig
  ) => void;
  select: (ref: string | null) => void;
  setCurrentSubcircuit: (id: string | null) => void;
  setViewport: (v: Partial<ViewportState>) => void;
  setComponentParams: (refdes: string, params: Partial<ComponentParams>) => void;
  setAnalysisConfig: (config: AnalysisConfig) => void;
  addProbe: (probe: Probe) => void;
  clearProbe: (index: number) => void;
  clearAllProbes: () => void;
}

const DEFAULT_ANALYSIS: AnalysisConfig = { type: "tran", step: "1u", stop: "1m" };
const DEFAULT_VIEWPORT: ViewportState = { pan: { x: 100, y: 100 }, zoom: 4 };

function loadSaved(): Partial<Pick<AppState, "schematic" | "componentParams" | "currentSubcircuitId" | "analysisConfig" | "probes">> {
  try {
    const raw = localStorage.getItem("spice-cockpit");
    if (!raw) return {};
    const data = JSON.parse(raw);
    return {
      schematic: data.schematic ?? null,
      componentParams: data.componentParams ?? {},
      currentSubcircuitId: data.schematic?.topLevel ?? null,
      analysisConfig: data.analysisConfig ?? DEFAULT_ANALYSIS,
      probes: data.probes ?? [null, null, null, null],
    };
  } catch {
    return {};
  }
}

export const useAppStore = create<AppState>((set) => ({
  schematic: null,
  selectedRefDes: null,
  currentSubcircuitId: null,
  viewport: DEFAULT_VIEWPORT,
  componentParams: {},
  analysisConfig: DEFAULT_ANALYSIS,
  probes: [null, null, null, null],
  ...loadSaved(),

  setSchematic: (s) =>
    set({ schematic: s, currentSubcircuitId: s.topLevel, selectedRefDes: null, viewport: DEFAULT_VIEWPORT }),

  loadProject: (schematic, componentParams, analysisConfig) =>
    set({
      schematic,
      componentParams,
      analysisConfig: analysisConfig ?? DEFAULT_ANALYSIS,
      currentSubcircuitId: schematic.topLevel,
      selectedRefDes: null,
      viewport: DEFAULT_VIEWPORT,
    }),

  select: (ref) => set({ selectedRefDes: ref }),
  setCurrentSubcircuit: (id) => set({ currentSubcircuitId: id }),
  setViewport: (v) =>
    set((state) => ({ viewport: { ...state.viewport, ...v } })),
  setComponentParams: (refdes, params) =>
    set((state) => ({
      componentParams: {
        ...state.componentParams,
        [refdes]: {
          spiceTemplate: state.componentParams[refdes]?.spiceTemplate ?? "",
          model: state.componentParams[refdes]?.model ?? "",
          notes: state.componentParams[refdes]?.notes ?? "",
          ...params,
        },
      },
    })),
  setAnalysisConfig: (config) => set({ analysisConfig: config }),

  addProbe: (probe) => set((state) => {
    if (probe.spiceNode === "0") return {};   // ground is always 0V, not useful
    const existing = state.probes.findIndex(p => p?.spiceNode === probe.spiceNode);
    if (existing !== -1) {
      const next = [...state.probes] as (Probe | null)[];
      next[existing] = null;
      return { probes: next };
    }
    const slot = state.probes.findIndex(p => p === null);
    if (slot === -1) return {};
    const next = [...state.probes] as (Probe | null)[];
    next[slot] = probe;
    return { probes: next };
  }),

  clearProbe: (index) => set((state) => {
    const next = [...state.probes] as (Probe | null)[];
    next[index] = null;
    return { probes: next };
  }),

  clearAllProbes: () => set({ probes: [null, null, null, null] }),
}));

// Cross-tab sync
const _bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("spice-cockpit") : null;

_bc?.addEventListener("message", (e) => {
  if (e.data?.type !== "update") return;
  useAppStore.setState(loadSaved());
});

// Autosave + broadcast when schematic, params, analysis config, or probes change.
let _s = useAppStore.getState().schematic;
let _p = useAppStore.getState().componentParams;
let _a = useAppStore.getState().analysisConfig;
let _pr = useAppStore.getState().probes;

useAppStore.subscribe((state) => {
  if (state.schematic === _s && state.componentParams === _p && state.analysisConfig === _a && state.probes === _pr) return;
  _s = state.schematic; _p = state.componentParams; _a = state.analysisConfig; _pr = state.probes;
  try {
    localStorage.setItem("spice-cockpit", JSON.stringify({
      schematic: state.schematic,
      componentParams: state.componentParams,
      analysisConfig: state.analysisConfig,
      probes: state.probes,
    }));
    _bc?.postMessage({ type: "update" });
  } catch { /* storage unavailable */ }
});
