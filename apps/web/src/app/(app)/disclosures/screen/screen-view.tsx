"use client";

import { useEffect, useMemo, useState } from "react";

import { CriteriaFields, LookThroughPanel, ScreenResults } from "@/components/disclosure/screen";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { dayLabel, money } from "@/lib/disclosure/coverage";
import { SAMPLE_AS_OF, SAMPLE_LOOK, SAMPLE_UNIVERSE } from "@/lib/disclosure/fixtures-analytics";
import { CRITERIA, parseForm, runScreen, type FormValues, type LookThrough, type ScreenResult } from "@/lib/disclosure/screen";

const SECTIONS = [
  { id: "screen", label: "Screen" },
  { id: "look", label: "Portfolio look-through" },
] as const;

const failure = (status: number, what: string) => (status === 401 || status === 403 ? "Sign in again (with MFA) and retry." : `${what} failed (HTTP ${status}).`);

/** Sample screen result built with the same rules as the backend, so the page can be shown without data. */
function sampleResult(query: Record<string, string | number | boolean>): ScreenResult {
  const r = runScreen(SAMPLE_UNIVERSE, query);
  const total = SAMPLE_UNIVERSE.reduce((s, h) => s + h.value, 0);
  const weight = (t: string) => String((SAMPLE_UNIVERSE.find((h) => h.ticker === t)?.value ?? 0) / total);
  const active = CRITERIA.filter((c) => query[c.key] !== undefined);
  const reason = (kind: "missing" | "failed") => ({ criterion: active[0]?.key ?? "", kind, reason: kind === "missing" ? "No value in the sample; not scored." : "Outside the bar in the sample.", value: null, threshold: "" });
  return {
    as_of: SAMPLE_AS_OF,
    snapshot_date: null,
    universe_basis: "Invented sample holdings",
    universe_size: r.universe,
    denominator_value: String(total),
    active_criteria: active.map((c) => c.key),
    matched: r.matched.map((t) => ({ ticker: t, weight: weight(t), values: [] })),
    excluded: [...r.missing.map((t) => ({ ticker: t, weight: weight(t), kind: "missing" as const, reasons: [reason("missing")] })), ...r.failed.map((t) => ({ ticker: t, weight: weight(t), kind: "failed" as const, reasons: [reason("failed")] }))].sort((a, b) => a.ticker.localeCompare(b.ticker)),
    matched_count: r.matched.length,
    excluded_missing: r.missing.length,
    excluded_failed: r.failed.length,
    matched_value: String(r.matched.reduce((s, t) => s + (SAMPLE_UNIVERSE.find((h) => h.ticker === t)?.value ?? 0), 0)),
    matched_weight: total > 0 ? String(r.matched.reduce((s, t) => s + (SAMPLE_UNIVERSE.find((h) => h.ticker === t)?.value ?? 0), 0) / total) : null,
    field_coverage: [],
    insider_window_days: 90,
    method_version: "screen.sample",
    notes: ["Sample: values are not shown for matched holdings."],
  };
}

/**
 * Screen the holdings on verified fields and read the portfolio's reach into confirmed relationships and deals.
 * A holding with no value for a chosen field is excluded and counted as missing; nothing is scored zero or ranked.
 */
export function ScreenPageView() {
  const [values, setValues] = useState<FormValues>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [sample, setSample] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [look, setLook] = useState<LookThrough | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [lookProblem, setLookProblem] = useState<string | null>(null);

  useEffect(() => {
    if (sample) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/portfolio/look-through", {});
        if (cancelled) return;
        if (r.data) {
          setLook(r.data);
          setLookProblem(null);
        } else setLookProblem(failure(r.response.status, "The look-through read"));
      } catch (e) {
        if (!cancelled) setLookProblem(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sample]);

  const shownLook = sample ? SAMPLE_LOOK : look;
  const parsed = useMemo(() => parseForm(values), [values]);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setErrors(parsed.errors);
    setProblem(null);
    if (parsed.errors.length) return;
    if (sample) return setResult(sampleResult(parsed.query));
    setBusy(true);
    try {
      const r = await getBrowserApiClient().GET("/disclosures/screen", { params: { query: parsed.query } });
      if (r.data) setResult(r.data);
      else setProblem(failure(r.response.status, "The screen"));
    } catch (err) {
      setProblem(`Backend unreachable: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Evidence screen"
        description="Screen what you hold on figures read from filings and reviewed by a person, and see how much of the portfolio is reached by confirmed relationships and deals."
        aside={shownLook ? <span>As known on {dayLabel(shownLook.as_of)} · value {money(shownLook.denominator_value)}</span> : undefined}
      />
      <p role="note" className="text-sm text-text-muted">
        Only reviewed values are used. A holding with no value for a field you chose is excluded and counted as missing data; it is never scored zero and the matches are not ranked.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={sample} onChange={(e) => { setSample(e.target.checked); setResult(null); setProblem(null); }} />
        <span>Show invented sample data instead of your holdings</span>
      </label>
      {sample ? <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm"><strong>Sample data.</strong> Four invented holdings. Nothing here is real.</p> : null}

      <SectionNav sections={SECTIONS} />
      <Panel id="screen" title="Screen" description="Choose any criteria; every one you set must hold for a holding to match.">
        <form onSubmit={run} noValidate className="space-y-3">
          <CriteriaFields values={values} onChange={setValues} disabled={busy} />
          {errors.length ? (
            <ul role="alert" className="space-y-0.5 border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
              {errors.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>{busy ? "Screening…" : "Run screen"}</Button>
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => { setValues({}); setErrors([]); setResult(null); }}>Clear</Button>
          </div>
        </form>
        {problem ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{problem}</p> : null}
        {result ? <ScreenResults result={result} /> : <p className="text-sm text-text-muted">No screen has been run yet.</p>}
      </Panel>

      <Panel id="look" title="Portfolio look-through" description="Direct and fund exposure to confirmed customer and supplier relationships and to deals. Weights are shares of the whole portfolio; where a share is not stated the weight stays unknown.">
        {lookProblem && !sample ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{lookProblem}</p> : null}
        {shownLook ? <LookThroughPanel data={shownLook} /> : !lookProblem ? <p className="text-sm text-text-muted">Loading…</p> : null}
      </Panel>
    </div>
  );
}
