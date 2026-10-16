"use client";

import { useEffect, useState } from "react";

import { BlindSpotList, CostAndRelease, CoverageBars, CoverageMatrix, FailureList, PeriodTable, QualityTable } from "@/components/disclosure/coverage";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { dayLabel, money, type CoverageReport } from "@/lib/disclosure/coverage";
import { SAMPLE_COVERAGE } from "@/lib/disclosure/fixtures-analytics";

const SECTIONS = [
  { id: "coverage", label: "Coverage by dataset" },
  { id: "matrix", label: "By jurisdiction" },
  { id: "periods", label: "By fiscal year" },
  { id: "blind", label: "Blind spots" },
  { id: "failures", label: "Collection failures" },
  { id: "quality", label: "Sampled error rates" },
  { id: "release", label: "Cost and release order" },
] as const;

type Load = { kind: "loading" } | { kind: "ready"; report: CoverageReport } | { kind: "error"; text: string };

/**
 * What the disclosure datasets cover over the tickers you hold, how old it is, where it is blind,
 * how often a sampled item was wrong, and what it costs. Covered means a record is stored, not that
 * nothing else exists; every share keeps its denominator and a missing one reads "unknown".
 */
export function CoveragePageView() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [sample, setSample] = useState(false);
  const [watchlist, setWatchlist] = useState(false);

  useEffect(() => {
    if (sample) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/data/coverage", { params: { query: { include_watchlist: watchlist } } });
        if (cancelled) return;
        if (r.data) setLoad({ kind: "ready", report: r.data });
        else setLoad({ kind: "error", text: r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `The coverage read failed (HTTP ${r.response.status}).` });
      } catch (e) {
        if (!cancelled) setLoad({ kind: "error", text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sample, watchlist]);

  const report = sample ? SAMPLE_COVERAGE : load.kind === "ready" ? load.report : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Data coverage"
        description="Per dataset and jurisdiction, how much of what you hold has a stored record, how old it is, where it is blind and how accurate it was when sampled."
        aside={report ? <span>As known on {dayLabel(report.as_of)} · {report.holdings} holdings · value {money(report.denominator_value)}</span> : undefined}
      />
      {sample ? null : (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={watchlist} onChange={(e) => { setLoad({ kind: "loading" }); setWatchlist(e.target.checked); }} className="size-4" />
          Include watchlist tickers I do not hold{report && report.watchlist_only > 0 ? ` (${report.watchlist_only} added; they carry no value in any share)` : ""}
        </label>
      )}
      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> An invented portfolio to demonstrate the page. Nothing here is real.{" "}
          <Button type="button" variant="outline" size="sm" onClick={() => { setLoad({ kind: "loading" }); setSample(false); }}>Back to your data</Button>
        </p>
      ) : null}
      {load.kind === "error" && !sample ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          {load.text}{" "}
          <Button type="button" variant="outline" size="sm" onClick={() => setSample(true)}>Show the labelled sample</Button>
        </p>
      ) : null}
      {load.kind === "loading" && !sample ? <p className="text-sm text-text-muted">Loading…</p> : null}

      {report ? (
        <>
          <SectionNav sections={SECTIONS} />
          <Panel id="coverage" title="Coverage by dataset" description="Holdings with at least one stored record known by the date, against all holdings, with the market value behind each count.">
            <CoverageBars report={report} />
          </Panel>
          <Panel id="matrix" title="By jurisdiction" description="Jurisdiction comes from the ISIN country, then the issuer country. A US result is never read as worldwide.">
            <CoverageMatrix report={report} />
          </Panel>
          <Panel id="periods" title="By fiscal year" description="Held issuers with reviewed financial observations for each year.">
            <PeriodTable report={report} />
          </Panel>
          <Panel id="blind" title="Known blind spots">
            <BlindSpotList report={report} />
          </Panel>
          <Panel id="failures" title="Collection failures" description="Authentication, retrieval, parse and paid-only gaps. A failure is a gap, not an alert.">
            <FailureList report={report} />
          </Panel>
          <Panel id="quality" title="Sampled error rates" description="From a person's labels at Adjudication, never from the classifier's own output. Wrong over labelled; an unsampled dataset has no rate.">
            <QualityTable report={report} />
          </Panel>
          <Panel id="release" title="Cost and release order">
            <CostAndRelease report={report} />
          </Panel>
          <ul className="space-y-0.5 text-xs text-text-muted">
            {report.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            <li>First record known {dayLabel(report.first_known_at)}, newest {dayLabel(report.last_known_at)} · {report.method_version}</li>
          </ul>
        </>
      ) : null}
    </div>
  );
}
