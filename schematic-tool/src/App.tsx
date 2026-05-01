import { useState } from "react";
import { importKicad } from "./importers/kicad";
import { useAppStore } from "./state/store";
import { SchematicView } from "./viewer/SchematicView";
import { PropertiesPanel } from "./panel/PropertiesPanel";
import { saveProject, clearSaveHandle, parseProjectFile } from "./project/io";

export default function App() {
  const setSchematic = useAppStore((s) => s.setSchematic);
  const loadProject = useAppStore((s) => s.loadProject);
  const schematic = useAppStore((s) => s.schematic);
  const componentParams = useAppStore((s) => s.componentParams);
  const analysisConfig = useAppStore((s) => s.analysisConfig);
  const probes = useAppStore((s) => s.probes);
  const clearAllProbes = useAppStore((s) => s.clearAllProbes);

  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);

  const handleKicadFile = async (file: File) => {
    setError(null);
    setFilename(file.name);
    clearSaveHandle();
    try {
      setSchematic(importKicad(await file.text()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleProjectFile = async (file: File) => {
    setError(null);
    clearSaveHandle();
    try {
      const project = parseProjectFile(await file.text());
      setFilename(file.name);
      loadProject(project.schematic, project.componentParams, project.analysisConfig);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onKicadChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleKicadFile(f);
    e.target.value = "";
  };

  const onProjectChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleProjectFile(f);
    e.target.value = "";
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (f.name.endsWith(".kicad_sch")) handleKicadFile(f);
    else if (f.name.endsWith(".json")) handleProjectFile(f);
  };

  const btn: React.CSSProperties = {
    background: "#444", padding: "5px 13px", borderRadius: 3,
    cursor: "pointer", userSelect: "none", whiteSpace: "nowrap",
    fontSize: 20, border: "none", color: "#eee",
    fontFamily: "system-ui, sans-serif",
  };

  return (
    <div
      style={{ width: "100vw", height: "100vh", display: "flex", flexDirection: "column" }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      {/* Toolbar */}
      <div style={{ padding: "8px 12px", background: "#222", color: "#eee", display: "flex", gap: 10, alignItems: "center", flexShrink: 0 }}>
        <label style={{ cursor: "pointer" }}>
          <input type="file" accept=".kicad_sch" onChange={onKicadChange} style={{ display: "none" }} />
          <span style={btn}>Open .kicad_sch</span>
        </label>
        <label style={{ cursor: "pointer" }}>
          <input type="file" accept=".json" onChange={onProjectChange} style={{ display: "none" }} />
          <span style={btn}>Open project</span>
        </label>
        {schematic && (
          <button
            onClick={() => saveProject(schematic, componentParams, analysisConfig, filename ?? "schematic")}
            style={{ ...btn, background: "#2a5a2a" }}
          >
            Save project
          </button>
        )}
        {schematic && (
          <button
            onClick={() => window.open(location.origin + location.pathname + "#/sim", "spice-sim")}
            style={{ ...btn, background: "#1a3a5a" }}
          >
            Open simulation ↗
          </button>
        )}
        {probes.some(Boolean) && (
          <button
            onClick={clearAllProbes}
            style={{ ...btn, background: "#3a1a1a" }}
          >
            Clear probes
          </button>
        )}
        <span style={{ opacity: 0.5, fontSize: 15 }}>{filename ?? "drag a .kicad_sch or .json anywhere"}</span>
        {error && <span style={{ color: "#f88", fontSize: 15 }}>Error: {error}</span>}
      </div>

      {/* Main area */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <SchematicView />
        </div>

        {/* Right column */}
        <div style={{
          width: 280, flexShrink: 0,
          borderLeft: "1px solid #d0d0d0",
          background: "#fafafa",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}>
          <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
            <PropertiesPanel />
          </div>
        </div>
      </div>
    </div>
  );
}
