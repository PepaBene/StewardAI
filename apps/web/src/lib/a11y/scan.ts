/**
 * A static scan for the accessibility rules that can be checked in source (M8.4): a data table has
 * a name, a column header says which way it heads, and a chart-like svg is hidden or described.
 * It cannot judge contrast, focus order or what a screen reader says; those need a browser pass.
 */

export type Finding = { rule: "table-name" | "th-scope" | "svg-name"; index: number };

const TABLE = /<table\b([^>]*)>([\s\S]*?)<\/table>/g;
const TH = /<th\b([^>]*)>/g;
const SVG = /<svg\b([^>]*)>/g;

const hasAny = (attrs: string, names: string[]) => names.some((n) => new RegExp(`\\b${n}\\s*=`).test(attrs) || attrs.includes(`{...${n}`));

export function scanSource(source: string): Finding[] {
  const out: Finding[] = [];
  for (const m of source.matchAll(TABLE)) {
    const named = hasAny(m[1], ["aria-label", "aria-labelledby"]) || /<caption\b/.test(m[2]);
    if (!named) out.push({ rule: "table-name", index: m.index ?? 0 });
  }
  for (const m of source.matchAll(TH)) {
    if (!hasAny(m[1], ["scope"])) out.push({ rule: "th-scope", index: m.index ?? 0 });
  }
  for (const m of source.matchAll(SVG)) {
    // A decorative svg is hidden; an informative one is named.
    if (!hasAny(m[1], ["aria-hidden", "aria-label", "aria-labelledby", "role"])) out.push({ rule: "svg-name", index: m.index ?? 0 });
  }
  return out;
}

export function countByRule(findings: Finding[]): Record<Finding["rule"], number> {
  const counts = { "table-name": 0, "th-scope": 0, "svg-name": 0 };
  for (const f of findings) counts[f.rule] += 1;
  return counts;
}
