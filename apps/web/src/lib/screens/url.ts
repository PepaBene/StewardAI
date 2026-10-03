import { isMetricId } from "./metrics";
import type { Criterion, MissingTreatment, Op, ScopeFilter } from "./types";

export type ShowFilter = "all" | "matched" | "failed" | "excluded";

export type ScreenState = {
  criteria: Criterion[];
  missing: MissingTreatment;
  scope: ScopeFilter;
  show: ShowFilter;
};

export const DEFAULT_SCREEN: ScreenState = { criteria: [], missing: "exclude", scope: "all", show: "all" };

type ParamsLike = { get(name: string): string | null };

const OPS: readonly Op[] = ["gte", "lte"];
export const MAX_CRITERIA = 8;

/** `pe:lte:20,roe:gte:15` -> criteria. A malformed or unknown-metric part is dropped. */
export function parseCriteria(raw: string | null): Criterion[] {
  if (!raw) return [];
  const out: Criterion[] = [];
  for (const part of raw.split(",")) {
    const [metric, op, ...rest] = part.split(":");
    if (!metric || !isMetricId(metric) || !OPS.includes(op as Op)) continue;
    out.push({ metric, op: op as Op, value: rest.join(":").slice(0, 16) });
    if (out.length >= MAX_CRITERIA) break;
  }
  return out;
}

export function parseScreenParams(params: ParamsLike): ScreenState {
  const missing = params.get("missing");
  const scope = params.get("scope");
  const show = params.get("show");
  return {
    criteria: parseCriteria(params.get("c")),
    missing: missing === "unknown" ? "unknown" : "exclude",
    scope: scope === "held" || scope === "watchlist" ? scope : "all",
    show: show === "matched" || show === "failed" || show === "excluded" ? show : "all",
  };
}

export function screenQuery(s: ScreenState): string {
  const q = new URLSearchParams();
  if (s.criteria.length) q.set("c", s.criteria.map((c) => `${c.metric}:${c.op}:${c.value}`).join(","));
  if (s.missing !== DEFAULT_SCREEN.missing) q.set("missing", s.missing);
  if (s.scope !== DEFAULT_SCREEN.scope) q.set("scope", s.scope);
  if (s.show !== DEFAULT_SCREEN.show) q.set("show", s.show);
  return q.toString().replace(/%3A/g, ":").replace(/%2C/g, ",");
}
