import { SafeLink } from "@/components/xray/safe-link";
import { stepFindings, type SkippedFiling } from "@/lib/jobs/step-findings";

function FilingList({ rows, label }: { rows: SkippedFiling[]; label: string }) {
  return (
    <ul className="m-0 list-none space-y-1 p-0" aria-label={label}>
      {rows.map((f) => (
        <li key={f.accession} className="text-xs">
          <span className="num font-mono">{f.accession}</span>
          {f.form ? <span className="text-text-muted"> · {f.form}</span> : null}
          <span className="block text-text">{f.cause}</span>
          {f.url ? (
            <SafeLink href={f.url} className="break-all text-accent-ink hover:underline">
              {f.url}
            </SafeLink>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * What a step's detail says in plain words: filings deferred to the next run (not an error),
 * filings skipped with their counts, and each filing the job could not read with the named cause
 * and the URL it tried. The raw JSON stays available below it.
 */
export function StepFindingsView({ detail }: { detail: Record<string, unknown> | null | undefined }) {
  const f = stepFindings(detail);
  if (!f) return null;
  return (
    <div className="space-y-2 border border-border-soft p-3" data-testid="step-findings">
      {f.deferred.length > 0 ? (
        <div className="space-y-1">
          <p className="text-sm font-medium">
            Deferred to the next run: {f.deferredTotal} filing{f.deferredTotal === 1 ? "" : "s"}
          </p>
          <p className="text-xs text-text-muted">
            Not an error and nothing is lost. {f.deferredReason ?? "The run stopped at its limit; the next run continues with the oldest filings first."}
          </p>
          <ul className="m-0 list-none space-y-0.5 p-0 text-xs" aria-label="Deferred filings by issuer">
            {f.deferred.map((d) => (
              <li key={d.cik} className="num">
                Issuer CIK {d.cik}: {d.count} filing{d.count === 1 ? "" : "s"} waiting
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {f.skippedCounts.length > 0 ? (
        <ul className="m-0 list-none space-y-0.5 p-0 text-xs" aria-label="Skipped filings">
          {f.skippedCounts.map((s) => (
            <li key={s.label} className="num">
              {s.count} {s.label}
            </li>
          ))}
        </ul>
      ) : null}
      {f.parseFailures.length > 0 ? (
        <div className="space-y-1">
          <p className="text-sm font-medium">
            {f.parseFailures.length} filing{f.parseFailures.length === 1 ? "" : "s"} could not be read
          </p>
          <FilingList rows={f.parseFailures} label="Filings that could not be read, with the cause" />
        </div>
      ) : null}
      {f.failures.length > 0 ? (
        <div className="space-y-1">
          <p className="text-sm font-medium">
            {f.failures.length} filing{f.failures.length === 1 ? "" : "s"} failed to load
          </p>
          <FilingList rows={f.failures} label="Filings that failed to load, with the error" />
        </div>
      ) : null}
    </div>
  );
}
