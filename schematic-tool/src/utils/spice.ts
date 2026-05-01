const SPICE_VALUE_RE = /^\d[\d.]*([pnuμmkMGT]([A-Za-z]{0,3})?)?$/i;

export function defaultTemplate(refdes: string, value: string): string {
  const prefix = (refdes.match(/^([A-Za-z]+)/)?.[1] ?? "X").toUpperCase();
  const v = value.trim() || null;
  switch (prefix) {
    case "R": return `R{refdes} {1} {2} ${v ?? "10k"}`;
    case "C": return `C{refdes} {1} {2} ${v ?? "100n"}`;
    case "L": return `L{refdes} {1} {2} ${v ?? "1u"}`;
    case "D": return `D{refdes} {1} {2} ${v ?? "1N4148"}`;
    case "Q": return `Q{refdes} {1} {2} {3} ${v ?? "2N3904"}`;
    case "M": return `M{refdes} {3} {2} {1} {1} ${v ?? "PMOS"}`;
    case "V": return `V{refdes} {1} {2} DC ${v ?? "5"}`;
    case "I": return `I{refdes} {1} {2} DC ${v ?? "1m"}`;
    default:  return `X{refdes} {1} {2} ${v ?? "SUBCKT"}`;
  }
}

// Refdes prefixes where the last template token is always a numeric value (R/C/L).
const NUMERIC_VALUE_PREFIXES = new Set(["R", "C", "L"]);

export function hasEditableValue(refdes: string): boolean {
  const prefix = (refdes.match(/^([A-Za-z]+)/)?.[1] ?? "").toUpperCase();
  return NUMERIC_VALUE_PREFIXES.has(prefix);
}

// Returns the primary editable value token (e.g. "10k") from a template,
// or null if the template is too complex to identify one (e.g. a model reference like "1N4148").
// Returns "" (empty string, not null) when the component should have a numeric value
// but none is present yet (e.g. R17 with value "~" imported from KiCad).
export function extractPrimaryToken(template: string, refdes?: string): string | null {
  const tokens = template.trim().split(/\s+/).slice(1);
  const candidates = tokens.filter((t) => !t.includes("{") && !t.includes("}"));
  const last = candidates[candidates.length - 1];
  if (last && SPICE_VALUE_RE.test(last)) return last;
  if (refdes && hasEditableValue(refdes)) return "";
  return null;
}

export function setPrimaryToken(template: string, newValue: string): string {
  const tokens = template.trim().split(/\s+/);
  // First pass: replace existing numeric value token
  for (let i = tokens.length - 1; i >= 1; i--) {
    const t = tokens[i];
    if (!t.includes("{") && !t.includes("}") && SPICE_VALUE_RE.test(t)) {
      return [...tokens.slice(0, i), newValue, ...tokens.slice(i + 1)].join(" ");
    }
  }
  // Second pass: replace any trailing non-placeholder token (e.g. "~" from KiCad)
  for (let i = tokens.length - 1; i >= 1; i--) {
    const t = tokens[i];
    if (!t.includes("{") && !t.includes("}")) {
      return [...tokens.slice(0, i), newValue, ...tokens.slice(i + 1)].join(" ");
    }
  }
  // No non-placeholder token found — append the value
  return `${template} ${newValue}`;
}

export function validateTemplate(template: string): string | null {
  let depth = 0;
  for (const ch of template) {
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth < 0) return "Unbalanced }";
    }
  }
  if (depth > 0) return "Unclosed {";
  for (const [, p] of template.matchAll(/\{([^}]+)\}/g)) {
    if (p !== "refdes" && !/^\d+$/.test(p)) return `Unknown placeholder: {${p}}`;
  }
  return null;
}

export function previewTemplate(template: string, refdes: string): string {
  return template
    .replace(/\{refdes\}/g, refdes)
    .replace(/\{(\d+)\}/g, (_, n) => `<net${n}>`);
}
