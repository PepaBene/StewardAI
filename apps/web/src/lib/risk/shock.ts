import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type ShockSpec = S["ShockSpec"];
export type ShockResult = S["ShockResult"];
export type ShockPosition = S["ShockPosition"];
export type Scenario = S["Scenario"];

export type ShockFieldId = "market" | "size" | "value" | "profitability" | "investment" | "momentum" | "usd" | "eur" | "rates_pp";

/** What the owner types for each factor, and how it is read. `percent` fields are typed as
 * percentages (-20 means -20 %); the yield is typed in points (1 means +1 point). */
export const SHOCK_FIELDS: readonly {
  id: ShockFieldId;
  label: string;
  hint: string;
  unit: "%" | "points";
  min: number;
  max: number;
}[] = [
  { id: "market", label: "Stock market", hint: "The whole equity market, such as -20", unit: "%", min: -95, max: 200 },
  { id: "size", label: "Small against large", hint: "Small companies minus large ones", unit: "%", min: -50, max: 50 },
  { id: "value", label: "Value against growth", hint: "Cheap shares minus expensive ones", unit: "%", min: -50, max: 50 },
  { id: "profitability", label: "Profitable against weak", hint: "Profitable firms minus unprofitable ones", unit: "%", min: -50, max: 50 },
  { id: "investment", label: "Cautious against heavy investors", hint: "Firms that invest cautiously minus heavy investors", unit: "%", min: -50, max: 50 },
  { id: "momentum", label: "Winners against losers", hint: "Recent winners minus recent losers", unit: "%", min: -50, max: 50 },
  { id: "usd", label: "US dollar", hint: "Against your base currency, -10 means the dollar falls 10 %", unit: "%", min: -50, max: 50 },
  { id: "eur", label: "Euro", hint: "Against your base currency", unit: "%", min: -50, max: 50 },
  { id: "rates_pp", label: "US 10-year yield", hint: "Change in points, 1 means up one point", unit: "points", min: -5, max: 5 },
];

/** A typed number, or null when the field is empty or not a number inside its range. Accepts a
 * decimal comma. */
export function parseField(text: string, min: number, max: number): number | null {
  const cleaned = text.trim().replace(",", ".");
  if (cleaned === "" || !/^[+-]?\d*\.?\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value >= min && value <= max ? value : null;
}

/** The request body for the typed fields: percentages become fractions, empty fields are left
 * out, and a field with unreadable text is reported instead of silently dropped. */
export function buildSpec(
  fields: Partial<Record<ShockFieldId, string>>,
  fallbackBeta: string,
  ownerBetas: readonly { ticker: string; beta: string }[],
): { spec: ShockSpec; problems: string[] } {
  const spec: ShockSpec = { owner_betas: {}, unmeasured_beta: 1 };
  const problems: string[] = [];
  for (const f of SHOCK_FIELDS) {
    const text = (fields[f.id] ?? "").trim();
    if (text === "") continue;
    const value = parseField(text, f.min, f.max);
    if (value === null) {
      problems.push(`${f.label}: enter a number from ${f.min} to ${f.max}`);
      continue;
    }
    spec[f.id] = f.unit === "%" ? value / 100 : value;
  }
  const fb = fallbackBeta.trim();
  if (fb === "") spec.unmeasured_beta = null;
  else {
    const v = parseField(fb, 0, 4);
    if (v === null) problems.push("Beta for unmeasured holdings: enter a number from 0 to 4, or leave it empty to leave them flat");
    else spec.unmeasured_beta = v;
  }
  for (const row of ownerBetas) {
    if (row.ticker.trim() === "" && row.beta.trim() === "") continue;
    const v = parseField(row.beta, 0, 4);
    if (row.ticker.trim() === "" || v === null) problems.push(`Own beta for ${row.ticker.trim() || "a holding"}: enter a ticker and a number from 0 to 4`);
    else spec.owner_betas = { ...spec.owner_betas, [row.ticker.trim()]: v };
  }
  return { spec, problems };
}

/** A one-line summary of a scenario's stated moves, "Stock market -20 %, US dollar -10 %". */
export function describeSpec(spec: ShockSpec): string {
  const parts: string[] = [];
  for (const f of SHOCK_FIELDS) {
    const v = spec[f.id];
    if (typeof v !== "number") continue;
    const shown = f.unit === "%" ? `${v > 0 ? "+" : ""}${Math.round(v * 1000) / 10} %` : `${v > 0 ? "+" : ""}${v} points`;
    parts.push(`${f.label} ${shown}`);
  }
  return parts.length ? parts.join(", ") : "No moves set";
}
