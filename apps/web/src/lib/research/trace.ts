/**
 * The research run's per-call trace (metadata only: no prompts, answers, pages or snippets).
 * Types are structural and read defensively, so reports stored before the trace existed (no
 * `trace`) and a trace the owner purged render nothing instead of failing.
 */

export type ModelCall = {
  step: string;
  section?: string | null;
  round?: number;
  model: string;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  searches?: number;
  cost_usd?: number | null;
  ms?: number;
  retries?: number;
  outcome?: "ok" | "failed" | "refused" | "schema_failure" | "skipped";
  note?: string | null;
};

export type WebStep = {
  step: string;
  section?: string | null;
  round?: number;
  queries?: string[];
  urls_found?: number;
  urls_cited?: number;
  urls_excluded?: number;
  found?: string[];
  opened?: string[];
  errors?: string[];
};

export type RetrievalStep = {
  section: string;
  sources_offered: number;
  sources_cited: number;
  sources_uncited?: number;
  tools_planned?: string[];
};

export type ValidationStep = {
  step: string;
  section?: string | null;
  schema_failure?: boolean;
  retries?: number;
  sentences_returned?: number;
  dropped_uncited?: number;
  links_removed?: number;
  dropped_by_check?: number;
  check_reasons?: string[];
};

export type RunTrace = {
  model_calls: ModelCall[];
  web: WebStep[];
  retrieval: RetrievalStep[];
  validation: ValidationStep[];
  total_cost_usd?: number | null;
  total_ms?: number;
  plan_source?: string | null;
};

/** The trace of a report body, or null for an older or purged report. */
export function traceOf(body: unknown): RunTrace | null {
  const raw = (body as { trace?: Partial<RunTrace> | null } | null | undefined)?.trace;
  if (!raw) return null;
  return {
    model_calls: raw.model_calls ?? [],
    web: raw.web ?? [],
    retrieval: raw.retrieval ?? [],
    validation: raw.validation ?? [],
    total_cost_usd: raw.total_cost_usd ?? null,
    total_ms: raw.total_ms ?? 0,
    plan_source: raw.plan_source ?? null,
  };
}

export const OUTCOME_LABELS: Record<string, string> = {
  ok: "ok",
  failed: "call failed",
  refused: "model declined",
  schema_failure: "failed the schema",
  skipped: "skipped",
};

export function outcomeLabel(outcome: string | undefined): string {
  return OUTCOME_LABELS[outcome ?? "ok"] ?? outcome ?? "ok";
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "no price";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatMs(ms: number | undefined): string {
  if (!ms) return "0 ms";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

/** One-line totals for the panel heading. */
export function traceTotals(trace: RunTrace): string[] {
  const tokens = trace.model_calls.reduce((n, c) => n + (c.input_tokens ?? 0) + (c.output_tokens ?? 0), 0);
  const failed = trace.model_calls.filter((c) => (c.outcome ?? "ok") !== "ok").length;
  const schema = trace.validation.filter((v) => v.schema_failure).length;
  const dropped = trace.validation.reduce((n, v) => n + (v.dropped_uncited ?? 0) + (v.dropped_by_check ?? 0), 0);
  const lines = [
    `${trace.model_calls.length} model call${trace.model_calls.length === 1 ? "" : "s"}, ${tokens.toLocaleString("en")} tokens, ${formatCost(trace.total_cost_usd)}, ${formatMs(trace.total_ms)}`,
  ];
  if (failed > 0) lines.push(`${failed} call${failed === 1 ? "" : "s"} did not produce a section`);
  if (schema > 0) lines.push(`${schema} answer${schema === 1 ? "" : "s"} failed the output schema`);
  if (dropped > 0) lines.push(`${dropped} sentence${dropped === 1 ? "" : "s"} dropped by code (no checkable source, or unsupported)`);
  return lines;
}

/** What validation did to one section, in words; empty when it kept everything. */
export function validationLine(v: ValidationStep): string {
  const parts: string[] = [];
  if (v.schema_failure) parts.push("answer failed the schema");
  if (v.dropped_uncited) parts.push(`${v.dropped_uncited} without a checkable source`);
  if (v.dropped_by_check) parts.push(`${v.dropped_by_check} removed by the claim check`);
  if (v.links_removed) parts.push(`${v.links_removed} link${v.links_removed === 1 ? "" : "s"} removed`);
  return parts.join(", ");
}
