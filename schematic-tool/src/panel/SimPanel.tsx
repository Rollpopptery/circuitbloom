import { useState } from "react";
import { useAppStore } from "../state/store";
import type { AnalysisConfig } from "../spice/formatter";
import { extractNetlist } from "../netlist/extract";
import { formatSpice } from "../spice/formatter";
import { runSimulation, type SimResult } from "../simulation/engine";

const TRACE_COLORS = ["#2196F3", "#e53935", "#43a047", "#FB8C00", "#8E24AA", "#00ACC1"];

function fmtNum(v: number): string {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e9)  return (v / 1e9).toPrecision(3) + "G";
  if (a >= 1e6)  return (v / 1e6).toPrecision(3) + "M";
  if (a >= 1e3)  return (v / 1e3).toPrecision(3) + "k";
  if (a >= 1)    return v.toPrecision(3);
  if (a >= 1e-3) return (v * 1e3).toPrecision(3) + "m";
  if (a >= 1e-6) return (v * 1e6).toPrecision(3) + "u";
  if (a >= 1e-9) return (v * 1e9).toPrecision(3) + "n";
  return (v * 1e12).toPrecision(3) + "p";
}

function WaveformPlot({ result }: { result: SimResult }) {
  const W = 248, H = 200, PAD = { t: 6, r: 4, b: 18, l: 38 };
  const pw = W - PAD.l - PAD.r, ph = H - PAD.t - PAD.b;

  const xData = result.data[0] ?? [];
  const traces = result.data.slice(1).map((d, i) => ({
    name: result.variableNames[i + 1],
    values: d,
    color: TRACE_COLORS[i % TRACE_COLORS.length],
  }));

  if (xData.length === 0 || traces.length === 0) return <div style={{ fontSize: 11, color: "#aaa", padding: 8 }}>No plottable data.</div>;

  const xMin = xData[0], xMax = xData[xData.length - 1];
  const allY = traces.flatMap((t) => t.values);
  let yMin = Math.min(...allY), yMax = Math.max(...allY);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }

  function sx(x: number) { return PAD.l + ((x - xMin) / (xMax - xMin)) * pw; }
  function sy(y: number) { return PAD.t + (1 - (y - yMin) / (yMax - yMin)) * ph; }

  function pathD(values: number[]) {
    return values
      .map((y, i) => `${i === 0 ? "M" : "L"}${sx(xData[i]).toFixed(1)} ${sy(y).toFixed(1)}`)
      .join(" ");
  }

  return (
    <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
      {/* Axes */}
      <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={PAD.t + ph} stroke="#ccc" strokeWidth={1} />
      <line x1={PAD.l} y1={PAD.t + ph} x2={PAD.l + pw} y2={PAD.t + ph} stroke="#ccc" strokeWidth={1} />
      {/* Y labels */}
      <text x={PAD.l - 3} y={PAD.t + 4} textAnchor="end" fontSize={9} fill="#999">{fmtNum(yMax)}</text>
      <text x={PAD.l - 3} y={PAD.t + ph} textAnchor="end" fontSize={9} fill="#999">{fmtNum(yMin)}</text>
      {/* X labels */}
      <text x={PAD.l} y={H - 2} textAnchor="middle" fontSize={9} fill="#999">{fmtNum(xMin)}</text>
      <text x={PAD.l + pw} y={H - 2} textAnchor="middle" fontSize={9} fill="#999">{fmtNum(xMax)}</text>
      {/* Traces */}
      {traces.map((t) => (
        <path key={t.name} d={pathD(t.values)} stroke={t.color} strokeWidth={1.5} fill="none" />
      ))}
    </svg>
  );
}

function Legend({ result }: { result: SimResult }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 10px", padding: "4px 0 8px" }}>
      {result.variableNames.slice(1).map((name, i) => (
        <span key={name} style={{ fontSize: 10, color: TRACE_COLORS[i % TRACE_COLORS.length], fontFamily: "monospace" }}>
          {name}
        </span>
      ))}
    </div>
  );
}

export function SimPanel() {
  const schematic = useAppStore((s) => s.schematic);
  const componentParams = useAppStore((s) => s.componentParams);
  const analysisConfig = useAppStore((s) => s.analysisConfig);
  const setAnalysisConfig = useAppStore((s) => s.setAnalysisConfig);

  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [result, setResult] = useState<SimResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [spiceText, setSpiceText] = useState<string | null>(null);
  const [showSpice, setShowSpice] = useState(false);

  const cfg = analysisConfig as Extract<AnalysisConfig, { type: "tran" }>;

  async function handleRun() {
    if (!schematic) return;
    setStatus("running");
    setError(null);
    setResult(null);
    try {
      const netlist = extractNetlist(schematic);
      const text = formatSpice(netlist, componentParams, analysisConfig);
      setSpiceText(text);
      const r = await runSimulation(text);
      setResult(r);
      setStatus("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  }

  const inp = (style?: React.CSSProperties): React.CSSProperties => ({
    fontFamily: "monospace",
    fontSize: 12,
    padding: "3px 6px",
    border: "1px solid #ccc",
    borderRadius: 3,
    background: "#fff",
    width: 64,
    boxSizing: "border-box",
    ...style,
  });

  return (
    <div style={{ borderTop: "2px solid #d0d0d0", background: "#f5f5f5", padding: "10px 12px 12px", flexShrink: 0 }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", color: "#999", textTransform: "uppercase", marginBottom: 8 }}>
        Simulation
      </div>

      {/* Analysis selector */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <select
          value={analysisConfig.type}
          onChange={(e) => {
            const t = e.target.value as AnalysisConfig["type"];
            if (t === "tran") setAnalysisConfig({ type: "tran", step: "1u", stop: "1m" });
            else if (t === "ac") setAnalysisConfig({ type: "ac", variation: "dec", points: 100, fstart: "1", fstop: "10Meg" });
            else setAnalysisConfig({ type: "dc", source: "V1", start: "0", stop: "5", step: "0.1" });
          }}
          style={{ fontSize: 12, padding: "3px 4px", border: "1px solid #ccc", borderRadius: 3, background: "#fff" }}
        >
          <option value="tran">Transient</option>
          <option value="ac">AC</option>
          <option value="dc">DC Sweep</option>
        </select>

        {analysisConfig.type === "tran" && (
          <>
            <input value={analysisConfig.stop} placeholder="stop"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, stop: e.target.value })}
              style={inp({ width: 52 })} title="Stop time (e.g. 1m)" />
            <input value={analysisConfig.step} placeholder="step"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, step: e.target.value })}
              style={inp({ width: 52 })} title="Time step (e.g. 1u)" />
          </>
        )}
        {analysisConfig.type === "ac" && (
          <>
            <input value={analysisConfig.fstart} placeholder="fstart"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, fstart: e.target.value })}
              style={inp()} title="Start freq" />
            <input value={analysisConfig.fstop} placeholder="fstop"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, fstop: e.target.value })}
              style={inp()} title="Stop freq" />
          </>
        )}
        {analysisConfig.type === "dc" && (
          <>
            <input value={analysisConfig.source} placeholder="source"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, source: e.target.value })}
              style={inp({ width: 44 })} title="Voltage source name" />
            <input value={analysisConfig.start} placeholder="start"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, start: e.target.value })}
              style={inp({ width: 44 })} />
            <input value={analysisConfig.stop} placeholder="stop"
              onChange={(e) => setAnalysisConfig({ ...analysisConfig, stop: e.target.value })}
              style={inp({ width: 44 })} />
          </>
        )}
      </div>

      <button
        onClick={handleRun}
        disabled={!schematic || status === "running"}
        style={{
          background: status === "running" ? "#888" : "#1976D2",
          color: "#fff", border: "none", borderRadius: 4,
          padding: "5px 14px", fontSize: 13, cursor: schematic ? "pointer" : "not-allowed",
          width: "100%", marginBottom: 8,
        }}
      >
        {status === "running" ? "Running…" : "▶ Run"}
      </button>

      {error && (
        <pre style={{ fontSize: 10, color: "#e53935", whiteSpace: "pre-wrap", wordBreak: "break-all", margin: "0 0 6px", maxHeight: 80, overflowY: "auto" }}>
          {error}
        </pre>
      )}

      {result && (
        <>
          <WaveformPlot result={result} />
          <Legend result={result} />
        </>
      )}

      {spiceText && (
        <>
          <button
            onClick={() => setShowSpice((v) => !v)}
            style={{ background: "none", border: "none", color: "#888", fontSize: 10, cursor: "pointer", padding: 0, marginBottom: 4, textDecoration: "underline" }}
          >
            {showSpice ? "hide SPICE" : "show SPICE"}
          </button>
          {showSpice && (
            <textarea
              readOnly
              value={spiceText}
              rows={6}
              style={{ width: "100%", boxSizing: "border-box", fontFamily: "monospace", fontSize: 10, border: "1px solid #ddd", borderRadius: 3, resize: "vertical", background: "#fff", color: "#333" }}
            />
          )}
        </>
      )}
    </div>
  );
}
