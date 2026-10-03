/*
 * Plain-wording findings read from a job step's `detail` (free-form JSON written by the job):
 * filings a job deferred for the next run, filings it skipped with the named cause, and runs the
 * system closed as interrupted. Only what the detail states is returned; a missing or malformed
 * field gives nothing (unknown is not zero).
 */

export type DeferredIssuer = { cik: string; count: number };
export type SkippedFiling = { accession: string; form: string | null; cause: string; url: string | null };

export type StepFindings = {
  /** Filings left for the next run, by issuer CIK (insider trades). Not an error. */
  deferred: DeferredIssuer[];
  deferredTotal: number;
  deferredReason: string | null;
  /** Filings the job could not read (13D/G): the named cause and the URL it tried. */
  parseFailures: SkippedFiling[];
  /** Filings that failed for a technical reason (download, database). */
  failures: SkippedFiling[];
  /** Counts of skipped filings by reason, as the job counted them. */
  skippedCounts: { label: string; count: number }[];
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const count = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);

/** The URL a reason ends with in square brackets ("cause [https://...]"), split from the cause. */
export function splitReasonUrl(reason: string): { cause: string; url: string | null } {
  const m = /^([\s\S]*?)\s*\[(https?:\/\/[^\]\s]+)\]\s*$/.exec(reason);
  return m ? { cause: m[1], url: m[2] } : { cause: reason, url: null };
}

function filings(value: unknown, causeKey: "reason" | "error"): SkippedFiling[] {
  if (!Array.isArray(value)) return [];
  const out: SkippedFiling[] = [];
  for (const row of value) {
    if (!isRecord(row) || typeof row.accession !== "string") continue;
    const text = typeof row[causeKey] === "string" && row[causeKey] !== "" ? (row[causeKey] as string) : null;
    const { cause, url } = text ? splitReasonUrl(text) : { cause: "no reason was recorded", url: null };
    out.push({ accession: row.accession, form: typeof row.form === "string" ? row.form : null, cause, url });
  }
  return out;
}

const SKIP_LABELS: [key: string, label: string][] = [
  ["unstructured_skipped", "older filings with no structured data, never readable"],
  ["not_held_skipped", "filings about companies you do not hold"],
  ["already_skipped", "filings skipped on an earlier run and not retried yet"],
  ["parse_failed", "filings that could not be read"],
];

export function stepFindings(detail: Record<string, unknown> | null | undefined): StepFindings | null {
  if (!detail || !isRecord(detail)) return null;
  const deferredRaw = detail.deferred_by_issuer;
  const deferred: DeferredIssuer[] = [];
  if (isRecord(deferredRaw)) {
    for (const [cik, n] of Object.entries(deferredRaw)) {
      const c = count(n);
      if (c !== null) deferred.push({ cik, count: c });
    }
  }
  const skippedCounts = SKIP_LABELS.flatMap(([key, label]) => {
    const c = count(detail[key]);
    return c !== null && c > 0 ? [{ label, count: c }] : [];
  });
  const found: StepFindings = {
    deferred,
    deferredTotal: deferred.reduce((a, d) => a + d.count, 0),
    deferredReason: typeof detail.deferred_reason === "string" ? detail.deferred_reason : null,
    parseFailures: filings(detail.parse_failures, "reason"),
    failures: filings(detail.failures, "error"),
    skippedCounts,
  };
  const any = deferred.length > 0 || found.parseFailures.length > 0 || found.failures.length > 0 || skippedCounts.length > 0;
  return any ? found : null;
}

/** A run the system closed because its process stopped (backend error text starts "interrupted"). */
export function isInterruptedError(error: string | null | undefined): boolean {
  return typeof error === "string" && /^interrupted\b/i.test(error.trim());
}

export const INTERRUPTED_EXPLANATION =
  "This run did not finish: the process that was running it stopped (a restart or a crash), and the system closed it afterwards. It is not a data error. Work that was already saved stays; the next run starts again and continues where this one left off.";
