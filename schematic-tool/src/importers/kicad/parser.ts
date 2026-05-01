// S-expression parser for KiCad files.
// Handles: nested lists, quoted strings (with escapes), numbers, atoms, comments.

export type SExpr = string | number | SExpr[];

export function parseSExpression(input: string): SExpr {
  const tokens = tokenize(input);
  const [result, nextIndex] = parseExpr(tokens, 0);
  if (nextIndex < tokens.length) {
    throw new Error(
      `Unexpected tokens after expression at position ${nextIndex}`,
    );
  }
  return result;
}

type Token =
  | { type: "lparen" }
  | { type: "rparen" }
  | { type: "string"; value: string }
  | { type: "atom"; value: string };

function tokenize(input: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;

  while (i < input.length) {
    const c = input[i];

    // Whitespace
    if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      i++;
      continue;
    }

    // Parens
    if (c === "(") {
      tokens.push({ type: "lparen" });
      i++;
      continue;
    }
    if (c === ")") {
      tokens.push({ type: "rparen" });
      i++;
      continue;
    }

    // Quoted string
    if (c === '"') {
      let value = "";
      i++; // skip opening quote
      while (i < input.length && input[i] !== '"') {
        if (input[i] === "\\" && i + 1 < input.length) {
          // simple escape: \" \\ \n \t
          const next = input[i + 1];
          if (next === "n") value += "\n";
          else if (next === "t") value += "\t";
          else value += next;
          i += 2;
        } else {
          value += input[i];
          i++;
        }
      }
      if (i >= input.length) throw new Error("Unterminated string");
      i++; // skip closing quote
      tokens.push({ type: "string", value });
      continue;
    }

    // Atom (number or symbol) — read until whitespace or paren
    let atom = "";
    while (
      i < input.length &&
      input[i] !== " " &&
      input[i] !== "\t" &&
      input[i] !== "\n" &&
      input[i] !== "\r" &&
      input[i] !== "(" &&
      input[i] !== ")"
    ) {
      atom += input[i];
      i++;
    }
    tokens.push({ type: "atom", value: atom });
  }

  return tokens;
}

function parseExpr(tokens: Token[], i: number): [SExpr, number] {
  const tok = tokens[i];
  if (!tok) throw new Error("Unexpected end of input");

  if (tok.type === "lparen") {
    const list: SExpr[] = [];
    i++;
    while (i < tokens.length && tokens[i].type !== "rparen") {
      const [child, next] = parseExpr(tokens, i);
      list.push(child);
      i = next;
    }
    if (i >= tokens.length) throw new Error("Unterminated list");
    return [list, i + 1]; // skip rparen
  }

  if (tok.type === "rparen") {
    throw new Error("Unexpected ')'");
  }

  if (tok.type === "string") {
    return [tok.value, i + 1];
  }

  // atom — try number, fall back to symbol
  const n = Number(tok.value);
  if (!isNaN(n) && tok.value.trim() !== "") {
    return [n, i + 1];
  }
  return [tok.value, i + 1];
}
