import type { ReactNode } from "react";
import type { ComponentInstance, Schematic, Symbol } from "../types";
import { useAppStore } from "../state/store";
import { SymbolView } from "../viewer/Symbol";
import { extractNetlist } from "../netlist/extract";
import {
  defaultTemplate,
  extractPrimaryToken,
  setPrimaryToken,
  validateTemplate,
  previewTemplate,
} from "../utils/spice";

function extractModelNames(modelText: string): string[] {
  return [...modelText.matchAll(/\.(?:model|subckt)\s+(\S+)/gi)].map(m => m[1].toLowerCase());
}

function findDuplicateModels(
  currentRefdes: string,
  currentModel: string,
  allParams: Record<string, { model?: string }>
): Array<{ name: string; owner: string }> {
  const currentNames = extractModelNames(currentModel);
  if (currentNames.length === 0) return [];
  const conflicts: Array<{ name: string; owner: string }> = [];
  for (const [refdes, params] of Object.entries(allParams)) {
    if (refdes === currentRefdes || !params.model) continue;
    for (const name of extractModelNames(params.model)) {
      if (currentNames.includes(name)) {
        conflicts.push({ name, owner: refdes });
      }
    }
  }
  return conflicts;
}

function findInstance(schematic: Schematic, refdes: string): ComponentInstance | null {
  for (const sub of Object.values(schematic.subcircuits)) {
    const found = sub.components.find((c) => c.refdes === refdes);
    if (found) return found;
  }
  return null;
}

function symbolViewBox(symbol: Symbol): string {
  if (symbol.bbox) {
    const { min, max } = symbol.bbox;
    const pad = 2;
    return `${min.x - pad} ${min.y - pad} ${max.x - min.x + pad * 2} ${max.y - min.y + pad * 2}`;
  }
  return "-8 -8 16 16";
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.08em",
        color: "#999",
        textTransform: "uppercase",
        padding: "10px 12px 4px",
      }}
    >
      {children}
    </div>
  );
}

function Divider() {
  return <div style={{ height: 1, background: "#e8e8e8", margin: "4px 0" }} />;
}

function EmptyPanel() {
  return (
    <div style={{ padding: "20px 14px", color: "#888", fontSize: 13 }}>
      <div style={{ fontWeight: 600, color: "#555", marginBottom: 6 }}>Project</div>
      <div>Click a component to inspect and edit its SPICE parameters.</div>
      <div style={{ marginTop: 14, color: "#bbb", fontSize: 11 }}>
        Analyses and global models will appear here in a future update.
      </div>
    </div>
  );
}

interface ComponentPanelProps {
  instance: ComponentInstance;
  schematic: Schematic;
}

function ComponentPanel({ instance, schematic }: ComponentPanelProps) {
  const componentParams = useAppStore((s) => s.componentParams);
  const setComponentParams = useAppStore((s) => s.setComponentParams);

  const symbol = schematic.symbols[instance.symbolId];

  // Pin ID → net name from extracted netlist
  const pinNets: Record<string, string> = (() => {
    try {
      const nl = extractNetlist(schematic);
      return nl.components.find(c => c.refdes === instance.refdes)?.pinNets ?? {};
    } catch { return {}; }
  })();
  const stored = componentParams[instance.refdes];
  const template = stored?.spiceTemplate ?? defaultTemplate(instance.refdes, instance.value);
  const model = stored?.model ?? "";
  const notes = stored?.notes ?? "";
  const probeI = stored?.probeI ?? false;

  const primaryToken = extractPrimaryToken(template, instance.refdes);
  const templateError = validateTemplate(template);
  const modelConflicts = findDuplicateModels(instance.refdes, model, componentParams);

  const colonIdx = instance.symbolId.indexOf(":");
  const libName = colonIdx >= 0 ? instance.symbolId.slice(0, colonIdx) : "—";
  const symbolName = colonIdx >= 0 ? instance.symbolId.slice(colonIdx + 1) : instance.symbolId;

  const setTemplate = (t: string) =>
    setComponentParams(instance.refdes, { spiceTemplate: t, model, notes, probeI });
  const setModel = (m: string) =>
    setComponentParams(instance.refdes, { spiceTemplate: template, model: m, notes, probeI });
  const setNotes = (n: string) =>
    setComponentParams(instance.refdes, { spiceTemplate: template, model, notes: n, probeI });
  const setProbeI = (v: boolean) =>
    setComponentParams(instance.refdes, { spiceTemplate: template, model, notes, probeI: v });

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div
        style={{
          padding: "12px",
          borderBottom: "1px solid #e0e0e0",
          display: "flex",
          gap: 10,
          alignItems: "center",
          background: "#fff",
        }}
      >
        {symbol && (
          <svg
            width={52}
            height={52}
            viewBox={symbolViewBox(symbol)}
            style={{
              border: "1px solid #e0e0e0",
              borderRadius: 4,
              background: "#fafafa",
              flexShrink: 0,
            }}
          >
            <SymbolView symbol={symbol} />
          </svg>
        )}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.1, color: "#111" }}>
            {instance.refdes}
          </div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {symbolName}
          </div>
          <div style={{ fontSize: 11, color: "#aaa" }}>{libName}</div>
        </div>
      </div>

      {/* Quick-edit value */}
      {primaryToken !== null && (
        <>
          <SectionLabel>Value</SectionLabel>
          <div style={{ padding: "0 12px 10px" }}>
            <input
              type="text"
              value={primaryToken}
              onChange={(e) => setTemplate(setPrimaryToken(template, e.target.value))}
              style={{
                width: "100%",
                boxSizing: "border-box",
                fontFamily: "monospace",
                fontSize: 15,
                padding: "5px 8px",
                border: "1px solid #ccc",
                borderRadius: 4,
                outline: "none",
              }}
            />
          </div>
          <Divider />
        </>
      )}

      {/* SPICE template */}
      <SectionLabel>SPICE Template</SectionLabel>
      <div style={{ padding: "0 12px 8px" }}>
        <textarea
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          rows={5}
          spellCheck={false}
          style={{
            width: "100%",
            boxSizing: "border-box",
            fontFamily: "monospace",
            fontSize: 12,
            padding: "6px 8px",
            border: `1px solid ${templateError ? "#e53935" : "#ccc"}`,
            borderRadius: 4,
            resize: "vertical",
            outline: "none",
            lineHeight: 1.5,
          }}
        />
        {templateError ? (
          <div style={{ color: "#e53935", fontSize: 11, marginTop: 3 }}>{templateError}</div>
        ) : (
          <div
            style={{
              fontFamily: "monospace",
              fontSize: 11,
              color: "#aaa",
              marginTop: 4,
              wordBreak: "break-all",
            }}
          >
            {previewTemplate(template, instance.refdes)}
          </div>
        )}
      </div>

      <Divider />

      {/* SPICE model definition */}
      <SectionLabel>Model</SectionLabel>
      <div style={{ padding: "0 12px 8px" }}>
        <textarea
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={`.model or .subckt block from vendor datasheet.\nLeave empty for standard primitives (R, C, L).`}
          rows={4}
          spellCheck={false}
          style={{
            width: "100%",
            boxSizing: "border-box",
            fontFamily: "monospace",
            fontSize: 11,
            padding: "6px 8px",
            border: `1px solid ${modelConflicts.length > 0 ? "#f57c00" : "#ccc"}`,
            borderRadius: 4,
            resize: "vertical",
            outline: "none",
            lineHeight: 1.5,
            color: model ? "#1a1a1a" : "#aaa",
          }}
        />
        {modelConflicts.length > 0 && (
          <div style={{ color: "#f57c00", fontSize: 11, marginTop: 3 }}>
            {modelConflicts.map(c =>
              `"${c.name}" already defined on ${c.owner}`
            ).join(" · ")}
          </div>
        )}
      </div>

      <Divider />

      {/* Pin → net table */}
      {symbol && symbol.pins.length > 0 && (
        <>
          <SectionLabel>Pin → Net</SectionLabel>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 12,
              marginBottom: 4,
            }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "2px 12px", fontWeight: 600, fontSize: 10, color: "#aaa", textTransform: "uppercase" }}>Placeholder</th>
                <th style={{ textAlign: "left", padding: "2px 4px", fontWeight: 600, fontSize: 10, color: "#aaa", textTransform: "uppercase" }}>Pin #</th>
                <th style={{ textAlign: "left", padding: "2px 4px", fontWeight: 600, fontSize: 10, color: "#aaa", textTransform: "uppercase" }}>Net</th>
              </tr>
            </thead>
            <tbody>
              {symbol.pins.map((pin) => {
                const net = pinNets[pin.name] ?? pinNets[pin.id];
                const primary = pin.name && pin.name !== pin.id ? `{${pin.name}}` : `{${pin.id}}`;
                const secondary = pin.name && pin.name !== pin.id ? ` / {${pin.id}}` : "";
                return (
                  <tr key={pin.id} style={{ borderTop: "1px solid #f0f0f0" }}>
                    <td style={{ padding: "3px 12px", color: "#1565C0", fontFamily: "monospace", fontWeight: 700 }}>
                      {primary}
                      {secondary && <span style={{ fontWeight: 400, color: "#999" }}>{secondary}</span>}
                    </td>
                    <td style={{ padding: "3px 4px", color: "#333", fontWeight: 600 }}>{pin.id}</td>
                    <td style={{ padding: "3px 4px", fontFamily: "monospace", color: net ? "#1565C0" : "#bbb" }}>
                      {net ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Divider />
        </>
      )}

      {/* Current probe */}
      <div style={{ padding: "6px 12px", display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" id="probeI" checked={probeI} onChange={e => setProbeI(e.target.checked)} />
        <label htmlFor="probeI" style={{ fontSize: 12, cursor: "pointer" }}>
          Probe current <span style={{ fontFamily: "monospace", color: "#1565C0" }}>i({instance.refdes.toLowerCase()})</span>
        </label>
      </div>
      <Divider />

      {/* Notes */}
      <SectionLabel>Notes</SectionLabel>
      <div style={{ padding: "0 12px 16px" }}>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Why this value, design intent…"
          rows={3}
          style={{
            width: "100%",
            boxSizing: "border-box",
            fontFamily: "sans-serif",
            fontSize: 12,
            padding: "6px 8px",
            border: "1px solid #ccc",
            borderRadius: 4,
            resize: "vertical",
            outline: "none",
            color: "#333",
          }}
        />
      </div>
    </div>
  );
}

export function PropertiesPanel() {
  const selectedRefDes = useAppStore((s) => s.selectedRefDes);
  const schematic = useAppStore((s) => s.schematic);

  const instance =
    selectedRefDes && schematic ? findInstance(schematic, selectedRefDes) : null;

  return instance && schematic ? (
    <ComponentPanel instance={instance} schematic={schematic} />
  ) : (
    <EmptyPanel />
  );
}
