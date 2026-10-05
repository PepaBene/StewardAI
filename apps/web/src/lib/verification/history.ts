/**
 * A signal's audit history from `/verification/signals/{name}/history`. Precision is null for an
 * audit that sampled nothing: that is a gap in the chart and never a zero.
 */
import { arr, isRecord, num, recs, str } from "@/lib/parse-untyped";

export type Audit = {
  id: number | null;
  at: string;
  precision: number | null;
  sample: number | null;
  correct: number | null;
  pitChecked: number | null;
  pitViolations: number | null;
  reviewer: string | null;
  jurisdiction: string | null;
  metric: string | null;
};

export type AuditHistory = { audits: Audit[]; latest: Audit | null; trend: "none" | "single" | "up" | "down" | "flat" };

export function parseHistory(payload: unknown): AuditHistory {
  const rows = isRecord(payload) ? recs(payload.audits) : recs(arr(payload));
  const audits: Audit[] = rows.flatMap((r) => {
    const at = str(r.audited_at);
    if (!at) return [];
    const sample = num(r.sample_size);
    const correct = num(r.evidence_correct);
    const given = num(r.evidence_precision);
    // Prefer the server's figure; recompute only when it is absent and the sample is non-empty.
    const precision = given ?? (sample !== null && sample > 0 && correct !== null ? correct / sample : null);
    return [
      {
        id: num(r.id),
        at,
        precision,
        sample,
        correct,
        pitChecked: num(r.pit_checked),
        pitViolations: num(r.pit_violations),
        reviewer: str(r.reviewer),
        jurisdiction: str(r.jurisdiction),
        metric: str(r.metric),
      },
    ];
  });
  audits.sort((a, b) => a.at.localeCompare(b.at));
  const measured = audits.filter((a) => a.precision !== null);
  let trend: AuditHistory["trend"] = "none";
  if (audits.length === 1) trend = "single";
  else if (measured.length >= 2) {
    const d = measured[measured.length - 1].precision! - measured[0].precision!;
    trend = Math.abs(d) < 0.005 ? "flat" : d > 0 ? "up" : "down";
  }
  return { audits, latest: audits[audits.length - 1] ?? null, trend };
}

export const TREND_TEXT: Record<AuditHistory["trend"], string> = {
  none: "No audits recorded",
  single: "One audit so far, so there is no trend yet",
  up: "Precision is higher than at the first audit",
  down: "Precision is lower than at the first audit",
  flat: "Precision is unchanged since the first audit",
};
