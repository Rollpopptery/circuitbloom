import { useState, useEffect, useRef } from "react";
import { useAppStore, PROBE_COLORS } from "../state/store";
import type { AnalysisConfig } from "../spice/formatter";
import { extractNetlist } from "../netlist/extract";
import { defaultTemplate } from "../utils/spice";
import { formatSpice } from "../spice/formatter";
import { runSimulation, type SimResult } from "../simulation/engine";

// ── helpers ────────────────────────────────────────────────────────────────

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

function niceTicks(min: number, max: number, n = 5): number[] {
  if (min === max) return [min];
  const rawStep = (max - min) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = ([1, 2, 5, 10].map(s => s * mag).find(s => (max - min) / s <= n + 1)) ?? mag;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 0.001; t += step)
    ticks.push(Math.round(t / step) * step);
  return ticks;
}

function useElementSize(ref: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState({ w: 600, h: 400 });
  useEffect(() => {
    if (!ref.current) return;
    const obs = new ResizeObserver(([e]) =>
      setSize({ w: Math.floor(e.contentRect.width), h: Math.floor(e.contentRect.height) })
    );
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);
  return size;
}

// ── waveform plot ──────────────────────────────────────────────────────────

interface Channel {
  name: string;
  color: string;
  values: number[];
}

interface View { x1: number; x2: number; y1: number; y2: number; }
interface PlotProps {
  xData: number[];
  channels: Channel[];
}

function WaveformPlot({ xData, channels }: PlotProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const { w, h } = useElementSize(containerRef);
  const PAD = { t: 16, r: 20, b: 44, l: 68 };
  const pw = w - PAD.l - PAD.r, ph = h - PAD.t - PAD.b;

  // Full data range
  let dataYMin = Infinity, dataYMax = -Infinity;
  for (const ch of channels)
    for (const v of ch.values)
      if (isFinite(v)) { if (v < dataYMin) dataYMin = v; if (v > dataYMax) dataYMax = v; }
  if (!isFinite(dataYMin)) { dataYMin = -1; dataYMax = 1; }
  if (dataYMin === dataYMax) { dataYMin -= 1; dataYMax += 1; }
  const dataXMin = xData[0] ?? 0, dataXMax = xData[xData.length - 1] ?? 1;
  const fullView: View = { x1: dataXMin, x2: dataXMax, y1: dataYMin, y2: dataYMax };

  const [view, setView] = useState<View | null>(null);
  const [drag, setDrag] = useState<{
    type: 'box' | 'pan';
    px0: number; py0: number;
    px1: number; py1: number;
    baseView: View;
  } | null>(null);

  // Reset view when a new simulation result arrives
  const xKey = `${dataXMin},${dataXMax},${xData.length}`;
  useEffect(() => { setView(null); setDrag(null); }, [xKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const committedView = view ?? fullView;
  let renderView = committedView;
  if (drag?.type === 'pan') {
    const dxData = -((drag.px1 - drag.px0) / pw) * (drag.baseView.x2 - drag.baseView.x1);
    const dyData = ((drag.py1 - drag.py0) / ph) * (drag.baseView.y2 - drag.baseView.y1);
    renderView = {
      x1: drag.baseView.x1 + dxData, x2: drag.baseView.x2 + dxData,
      y1: drag.baseView.y1 + dyData, y2: drag.baseView.y2 + dyData,
    };
  }

  const { x1: xMin, x2: xMax, y1: yMin, y2: yMax } = renderView;
  const sx = (x: number) => PAD.l + ((x - xMin) / (xMax - xMin)) * pw;
  const sy = (y: number) => PAD.t + (1 - (y - yMin) / (yMax - yMin)) * ph;
  const invX = (px: number) => xMin + ((px - PAD.l) / pw) * (xMax - xMin);
  const invY = (py: number) => yMax - ((py - PAD.t) / ph) * (yMax - yMin);

  const xTicks = niceTicks(xMin, xMax, Math.max(3, Math.floor(pw / 100)));
  const yTicks = niceTicks(yMin, yMax, Math.max(3, Math.floor(ph / 60)));

  function svgXY(e: React.MouseEvent): [number, number] {
    const r = svgRef.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  function handleMouseDown(e: React.MouseEvent) {
    if (e.button === 2) e.preventDefault();
    const [px, py] = svgXY(e);
    const type = e.button === 2 ? 'pan' : 'box';
    setDrag({ type, px0: px, py0: py, px1: px, py1: py, baseView: renderView });
  }

  function handleMouseMove(e: React.MouseEvent) {
    if (!drag) return;
    const [px, py] = svgXY(e);
    setDrag(d => d ? { ...d, px1: px, py1: py } : null);
  }

  function handleMouseUp() {
    if (!drag) return;
    if (drag.type === 'box') {
      const dx = Math.abs(drag.px1 - drag.px0), dy = Math.abs(drag.py1 - drag.py0);
      if (dx > 8 || dy > 8) {
        const left = Math.min(drag.px0, drag.px1), right = Math.max(drag.px0, drag.px1);
        const top = Math.min(drag.py0, drag.py1), bottom = Math.max(drag.py0, drag.py1);
        setView({ x1: invX(left), x2: invX(right), y1: invY(bottom), y2: invY(top) });
      }
    } else {
      setView(renderView);
    }
    setDrag(null);
  }

  const boxRect = drag?.type === 'box' ? {
    x: Math.min(drag.px0, drag.px1), y: Math.min(drag.py0, drag.py1),
    width: Math.abs(drag.px1 - drag.px0), height: Math.abs(drag.py1 - drag.py0),
  } : null;

  const cursor = drag?.type === 'pan' ? 'grabbing' : 'crosshair';

  return (
    <div ref={containerRef} style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: "hidden" }}>
      {w > 0 && (
        <svg ref={svgRef} width={w} height={h}
          style={{ display: "block", cursor, userSelect: "none" }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onDoubleClick={() => setView(null)}
          onContextMenu={e => e.preventDefault()}
        >
          {/* grid */}
          {yTicks.map(t => (
            <line key={t} x1={PAD.l} x2={PAD.l + pw} y1={sy(t)} y2={sy(t)} stroke="#333" strokeWidth={1} />
          ))}
          {xTicks.map(t => (
            <line key={t} x1={sx(t)} x2={sx(t)} y1={PAD.t} y2={PAD.t + ph} stroke="#333" strokeWidth={1} />
          ))}
          {/* axes */}
          <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={PAD.t + ph} stroke="#555" strokeWidth={1} />
          <line x1={PAD.l} y1={PAD.t + ph} x2={PAD.l + pw} y2={PAD.t + ph} stroke="#555" strokeWidth={1} />
          {/* y tick labels */}
          {yTicks.map(t => (
            <text key={t} x={PAD.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill="#888">{fmtNum(t)}</text>
          ))}
          {/* x tick labels */}
          {xTicks.map(t => (
            <text key={t} x={sx(t)} y={PAD.t + ph + 16} textAnchor="middle" fontSize={11} fill="#888">{fmtNum(t)}</text>
          ))}
          {/* zero line */}
          {yMin < 0 && yMax > 0 && (
            <line x1={PAD.l} x2={PAD.l + pw} y1={sy(0)} y2={sy(0)} stroke="#555" strokeWidth={1} strokeDasharray="4 2" />
          )}
          {/* traces — min/max decimated to pixel width so large datasets don't freeze */}
          {channels.map(ch => {
            const maxPts = Math.max(pw * 2, 500);
            const values = ch.values;
            let pts: { x: number; y: number }[];
            if (values.length <= maxPts) {
              pts = values.map((y, i) => ({ x: xData[i], y }));
            } else {
              const bucketSize = Math.ceil(values.length / (maxPts / 2));
              pts = [];
              for (let b = 0; b < values.length; b += bucketSize) {
                const slice = values.slice(b, b + bucketSize);
                const xs = xData.slice(b, b + bucketSize);
                const minIdx = slice.reduce((mi, v, i) => v < slice[mi] ? i : mi, 0);
                const maxIdx = slice.reduce((mi, v, i) => v > slice[mi] ? i : mi, 0);
                const [first, second] = minIdx < maxIdx ? [minIdx, maxIdx] : [maxIdx, minIdx];
                pts.push({ x: xs[first], y: slice[first] });
                pts.push({ x: xs[second], y: slice[second] });
              }
            }
            const d = pts.map((p, i) =>
              `${i === 0 ? "M" : "L"}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`
            ).join(" ");
            return <path key={ch.name} d={d} stroke={ch.color} strokeWidth={2} fill="none" />;
          })}
          {/* box select overlay */}
          {boxRect && (
            <rect x={boxRect.x} y={boxRect.y} width={boxRect.width} height={boxRect.height}
              fill="rgba(100,160,255,0.12)" stroke="rgba(100,160,255,0.8)" strokeWidth={1} strokeDasharray="4 2" />
          )}
          {/* hint */}
          {!view && (
            <text x={PAD.l + pw} y={PAD.t - 2} textAnchor="end" fontSize={10} fill="#555">drag to zoom · right-drag to pan · dbl-click to reset</text>
          )}
        </svg>
      )}
    </div>
  );
}

// ── main view ──────────────────────────────────────────────────────────────

export function SimView() {
  const schematic = useAppStore(s => s.schematic);
  const componentParams = useAppStore(s => s.componentParams);
  const analysisConfig = useAppStore(s => s.analysisConfig);
  const setAnalysisConfig = useAppStore(s => s.setAnalysisConfig);
  const probes = useAppStore(s => s.probes);
  const clearProbe = useAppStore(s => s.clearProbe);

  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [result, setResult] = useState<SimResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [spiceText, setSpiceText] = useState<string | null>(null);
  const [showSpice, setShowSpice] = useState(false);

  const activeProbes = probes.map((p, i) => p ? { ...p, index: i, color: PROBE_COLORS[i] } : null);
  const saveNets = activeProbes.filter(Boolean).map(p => p!.spiceNode).filter(n => n !== "0");

  async function handleRun() {
    if (!schematic) return;
    setStatus("running"); setError(null);
    try {
      const netlist = extractNetlist(schematic);
      const text = formatSpice(netlist, componentParams, analysisConfig, "Schematic", saveNets);
      setSpiceText(text);
      const r = await runSimulation(text);
      console.log("ngspice variables:", r.variableNames);
      setResult(r); setStatus("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setStatus("error");
    }
  }

  // Map result variable names to channels for probed nets
  function buildChannels(): { xData: number[]; channels: Channel[] } {
    if (!result) return { xData: [], channels: [] };
    const xData = result.data[0] ?? [];
    const channels: Channel[] = [];

    for (const ap of activeProbes) {
      if (!ap) continue;
      const varIdx = result.variableNames.findIndex(
        n => n.toLowerCase() === `v(${ap.spiceNode.toLowerCase()})` ||
             n.toLowerCase() === ap.spiceNode.toLowerCase()
      );
      if (varIdx > 0 && result.data[varIdx]) {
        channels.push({ name: ap.netName, color: ap.color, values: result.data[varIdx] });
      }
    }

    // Current probes from checked components
    const CURRENT_COLORS = ["#FF8800", "#00FF88", "#FF0088", "#8800FF"];
    let colorIdx = 0;
    for (const comp of (schematic ? extractNetlist(schematic).components : [])) {
      const params = componentParams[comp.refdes];
      if (!params?.probeI) continue;
      const template = params.spiceTemplate ?? defaultTemplate(comp.refdes, comp.value);
      const resolved = template.replace(/\{refdes\}/g, comp.refdes).replace(/\{(\d+)\}/g, (_, n) => comp.pinNets[n] ?? `_NC${n}`);
      const elementName = resolved.trim().split(/\s+/)[0].toLowerCase();
      const iVar = `i(${elementName})`;
      const varIdx = result.variableNames.findIndex(n => n.toLowerCase() === iVar);
      if (varIdx > 0 && result.data[varIdx]) {
        channels.push({ name: iVar, color: CURRENT_COLORS[colorIdx++ % CURRENT_COLORS.length], values: result.data[varIdx] });
      }
    }

    return { xData, channels };
  }

  const { xData, channels } = buildChannels();

  const inpStyle: React.CSSProperties = {
    fontFamily: "monospace", fontSize: 12, padding: "3px 6px",
    border: "1px solid #555", borderRadius: 3, background: "#2a2a2a", color: "#eee", width: 68,
  };

  return (
    <div style={{ width: "100vw", height: "100vh", display: "flex", flexDirection: "column", background: "#1a1a1a", color: "#eee", fontFamily: "sans-serif" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "#222", borderBottom: "1px solid #333", flexShrink: 0, flexWrap: "wrap" }}>

        <select value={analysisConfig.type}
          onChange={e => {
            const t = e.target.value as AnalysisConfig["type"];
            if (t === "tran") setAnalysisConfig({ type: "tran", step: "1u", stop: "1m" });
            else if (t === "ac") setAnalysisConfig({ type: "ac", variation: "dec", points: 100, fstart: "1", fstop: "10Meg" });
            else setAnalysisConfig({ type: "dc", source: "V1", start: "0", stop: "5", step: "0.1" });
          }}
          style={{ ...inpStyle, width: "auto" }}>
          <option value="tran">Transient</option>
          <option value="ac">AC</option>
          <option value="dc">DC Sweep</option>
        </select>

        {analysisConfig.type === "tran" && <>
          <input value={analysisConfig.stop} onChange={e => setAnalysisConfig({ ...analysisConfig, stop: e.target.value })} style={inpStyle} title="Stop time — units: s, m=ms, u=µs, n=ns, p=ps (e.g. 1m, 500u)" placeholder="stop" />
          <input value={analysisConfig.step} onChange={e => setAnalysisConfig({ ...analysisConfig, step: e.target.value })} style={inpStyle} title="Time step — units: s, m=ms, u=µs, n=ns, p=ps (e.g. 1u, 10n)" placeholder="step" />
          <input value={analysisConfig.tmax ?? ""} onChange={e => setAnalysisConfig({ ...analysisConfig, tmax: e.target.value || undefined })} style={inpStyle} title="Max internal timestep — forces solver to not skip fast transients (e.g. 1n to capture µs spikes). Leave blank for auto." placeholder="tmax" />
        </>}
        {analysisConfig.type === "ac" && <>
          <input value={analysisConfig.fstart} onChange={e => setAnalysisConfig({ ...analysisConfig, fstart: e.target.value })} style={inpStyle} title="Start freq" placeholder="fstart" />
          <input value={analysisConfig.fstop} onChange={e => setAnalysisConfig({ ...analysisConfig, fstop: e.target.value })} style={inpStyle} title="Stop freq" placeholder="fstop" />
        </>}
        {analysisConfig.type === "dc" && <>
          <input value={analysisConfig.source} onChange={e => setAnalysisConfig({ ...analysisConfig, source: e.target.value })} style={{ ...inpStyle, width: 50 }} placeholder="src" />
          <input value={analysisConfig.start} onChange={e => setAnalysisConfig({ ...analysisConfig, start: e.target.value })} style={{ ...inpStyle, width: 50 }} placeholder="start" />
          <input value={analysisConfig.stop} onChange={e => setAnalysisConfig({ ...analysisConfig, stop: e.target.value })} style={{ ...inpStyle, width: 50 }} placeholder="stop" />
        </>}

        <button onClick={handleRun} disabled={!schematic || status === "running" || saveNets.length === 0}
          style={{ background: (status === "running" || saveNets.length === 0) ? "#555" : "#1565C0", color: "#fff", border: "none", borderRadius: 4, padding: "5px 18px", fontSize: 13, cursor: "pointer", fontWeight: 600 }}>
          {status === "running" ? "Running…" : "▶ Run"}
        </button>

        {status === "done" && <span style={{ fontSize: 12, color: "#66bb6a" }}>✓ {result?.numPoints} pts</span>}
        {status === "error" && <span style={{ fontSize: 12, color: "#ef5350" }}>Error</span>}
        {saveNets.length === 0 && <span style={{ fontSize: 12, color: "#888" }}>Place probes on wires in the schematic editor</span>}
      </div>

      {/* Body */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        {/* Probe channel strip */}
        <div style={{ display: "flex", gap: 8, padding: "8px 14px", borderBottom: "1px solid #2a2a2a", flexShrink: 0, flexWrap: "wrap" }}>
          {activeProbes.map((p, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "#252525", border: `1px solid ${p ? PROBE_COLORS[i] : "#333"}`,
              borderRadius: 4, padding: "4px 10px", minWidth: 100,
              opacity: p ? 1 : 0.35,
            }}>
              <span style={{
                width: 18, height: 18, borderRadius: "50%",
                background: PROBE_COLORS[i], display: "inline-flex",
                alignItems: "center", justifyContent: "center",
                fontSize: 11, fontWeight: "bold", color: "#000", flexShrink: 0,
              }}>{i + 1}</span>
              <span style={{ fontFamily: "monospace", fontSize: 12, color: p ? PROBE_COLORS[i] : "#555", flex: 1 }}>
                {p ? p.netName : "—"}
              </span>
              {p && (
                <button onClick={() => clearProbe(i)}
                  style={{ background: "none", border: "none", color: "#666", fontSize: 14, cursor: "pointer", padding: 0, lineHeight: 1 }}
                  title="Remove probe">×</button>
              )}
            </div>
          ))}
        </div>

        {/* Plot area */}
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: 16 }}>
          {!result && !error && (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", fontSize: 14 }}>
              {saveNets.length === 0
                ? "Click wires in the schematic to place up to 4 probes, then run."
                : schematic ? "Configure analysis and hit Run." : "Open a schematic in the editor tab first."}
            </div>
          )}
          {error && (
            <pre style={{ color: "#ef5350", fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all", flex: 1, overflowY: "auto" }}>{error}</pre>
          )}
          {result && channels.length > 0 && (
            <WaveformPlot xData={xData} channels={channels} />
          )}
          {result && channels.length === 0 && (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", fontSize: 14 }}>
              No probe data in result. Re-run after placing probes.
            </div>
          )}

          {spiceText && (
            <div style={{ flexShrink: 0, marginTop: 8 }}>
              <button onClick={() => setShowSpice(v => !v)}
                style={{ background: "none", border: "none", color: "#666", fontSize: 22, cursor: "pointer", padding: 0, textDecoration: "underline" }}>
                {showSpice ? "hide SPICE" : "show SPICE"}
              </button>
              {showSpice && (
                <textarea readOnly value={spiceText} rows={8}
                  style={{ display: "block", width: "100%", boxSizing: "border-box", marginTop: 4, fontFamily: "monospace", fontSize: 11, background: "#111", color: "#ccc", border: "1px solid #333", borderRadius: 3, resize: "vertical" }} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
