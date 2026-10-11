"use client";

import { FlagIcon, LightbulbIcon, LoaderCircleIcon, RefreshCwIcon, ShieldCheckIcon, TriangleAlertIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Ring } from "@/components/charts";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { SourceChips, SourceSidebar } from "@/components/sources/source-list";
import { ReviewShell } from "@/components/reports/review-shell";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ReviewReportOut, type StrategyConfig } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { jobHref } from "@/lib/jobs/job-detail";
import {
  actionBasis,
  COMPLIANCE_KIND,
  COMPLIANCE_STATUS,
  complianceAnchor,
  complianceCounts,
  kpiAnchor,
  missingRules,
  monthLabel,
  reviewIndex,
  sortCompliance,
  strategyVersionOf,
} from "@/lib/review/monthly";

const LINK = "text-accent-ink underline-offset-4 hover:underline";
const SECTION = "blueprint space-y-3 px-6 py-5";

/** /reviews/[id]: the reviews list beside this review. */
export function MonthlyReviewView({ id }: { id: number }) {
  return <ReviewShell selectedId={id}>{() => <MonthlyReviewDetail id={id} />}</ReviewShell>;
}

/** One monthly review by id, without the page frame. */
export function MonthlyReviewDetail({ id }: { id: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/reviews/{report_id}", { params: { path: { report_id: id } } }),
    [id],
  );
  const { state, reload } = useApiResource(fetcher);
  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading monthly review…
      </p>
    );
  }
  if (state.kind === "error" && !state.previous) {
    return (
      <AlertMessage title={state.status === 404 ? `No monthly review #${id}` : state.title}>
        <p>{state.status === 404 ? "The API has no review with this number." : state.detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Try again
        </Button>
      </AlertMessage>
    );
  }
  const report = state.kind === "ok" ? state.data : state.previous!;
  return <Review report={report} />;
}

type StrategyLoad =
  | { kind: "loading" }
  | { kind: "ok"; strategy: Pick<StrategyConfig, "rules"> | null; version: number | null; current: boolean }
  | { kind: "error"; message: string };

/**
 * The strategy the review was judged against (its version, from the review's strategy
 * source), else the active one, to check that the table lists every rule.
 */
function useReviewStrategy(report: ReviewReportOut): StrategyLoad {
  const version = strategyVersionOf(report.body.sources);
  const [state, setState] = useState<StrategyLoad>({ kind: "loading" });
  useEffect(() => {
    let cancelled = false;
    const api = getBrowserApiClient();
    (async () => {
      try {
        if (version !== null) {
          const { data } = await api.GET("/config/{domain}/versions/{version_id}", {
            params: { path: { domain: "strategy", version_id: version } },
          });
          if (data) {
            if (!cancelled) setState({ kind: "ok", strategy: data.body as Pick<StrategyConfig, "rules">, version, current: false });
            return;
          }
        }
        const { data, response } = await api.GET("/config/strategy");
        if (cancelled) return;
        if (data) setState({ kind: "ok", strategy: data.body, version: data.id, current: true });
        else if (response.status === 404) setState({ kind: "ok", strategy: null, version: null, current: true });
        else setState({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      } catch (err) {
        if (!cancelled) setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [version]);
  return state;
}

function Review({ report }: { report: ReviewReportOut }) {
  const { body } = report;
  const index = useMemo(() => reviewIndex(body), [body]);
  const rows = useMemo(() => sortCompliance(body.compliance), [body.compliance]);
  const counts = complianceCounts(body.compliance);
  const strategy = useReviewStrategy(report);
  const missing = strategy.kind === "ok" ? missingRules(strategy.strategy, body.compliance) : [];
  const ruleCount = strategy.kind === "ok" ? (strategy.strategy?.rules ?? []).length : null;

  return (
    <div className="flex flex-col gap-5">
      <section aria-labelledby="review-headline" className="blueprint flex flex-wrap items-end justify-between gap-4 px-6 py-5">
        <BlueprintCorners />
        <div className="min-w-0">
          <p className="kicker">Monthly review · {monthLabel(body.month)}</p>
          <h2 id="review-headline" className="mt-1 text-[32px] leading-[1.05] font-semibold">{body.headline || report.title}</h2>
          <p className="num mt-1 text-sm text-text-muted">
            {body.as_of ? <>Judged on the snapshot of {formatDate(body.as_of)}</> : "No portfolio snapshot"}
            {body.total_base !== null && body.base_currency ? <> · portfolio {formatMoney(body.total_base, body.base_currency, { decimals: 0 })}</> : null}
            {" · "}written {formatDateTime(report.created_at)}
            {body.model ? <> · actions proposed by <span className="font-mono">{body.model}</span></> : " · no model"}
            {report.job_id !== null ? (
              <>
                {" · "}
                <Link href={jobHref(report.job_id)} className={LINK}>
                  job #{report.job_id}
                </Link>
              </>
            ) : null}
          </p>
        </div>
        <div className="flex flex-col items-end gap-3">
          {body.compliance.length > 0 ? (
            <div className="flex items-center gap-3">
              <Ring value={counts.pass} max={body.compliance.length} label="" size={46} thickness={5} ariaLabel="Strategy checks passing" />
              <p className="text-xs text-text-muted">
                <span className="num block font-heading text-2xl leading-none font-semibold text-text">
                  {counts.pass} / {body.compliance.length}
                </span>
                checks pass
              </p>
            </div>
          ) : null}
          <AsOfLabel at={report.created_at} kind="escalation" prefix="Written" />
        </div>
      </section>

      {(body.notes ?? []).length > 0 ? (
        <AlertMessage tone="info" title="Notes on this review">
          <ul className="list-disc space-y-0.5 pl-5">
            {(body.notes ?? []).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </AlertMessage>
      ) : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <article className="flex flex-col gap-5">
          <section aria-labelledby="compliance-heading" className={SECTION}>
            <BlueprintCorners />
            <h2 id="compliance-heading" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
              <ShieldCheckIcon aria-hidden="true" className="size-4 text-text-muted" />
              Strategy compliance
            </h2>
            <p className="num text-sm text-text-muted">
              {body.compliance.length} checks, computed in code: {counts.breach} breach · {counts.unknown} unknown · {counts.pass} pass.
            </p>
            <RuleCoverage strategy={strategy} missing={missing} ruleCount={ruleCount} />
            {body.compliance.length === 0 ? (
              <p className="text-sm text-text-muted">The strategy had no rules, hard limits or target bands to check.</p>
            ) : (
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[36rem] text-sm">
                  <thead>
                    <tr className="border-b border-border text-text-muted">
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Status</th>
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Check</th>
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Detail</th>
                      <th scope="col" className="py-1.5 text-left font-medium">Sources</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} id={complianceAnchor(r.id)} className="scroll-mt-24 border-b border-border align-top last:border-0 target:bg-surface-muted">
                        <td className="py-1.5 pr-3" title={COMPLIANCE_STATUS[r.status].description}>
                          <VerdictStatusBadge status={r.status} />
                        </td>
                        <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                          <span className="font-medium">{r.description}</span>
                          <span className="block text-xs text-text-muted">
                            {COMPLIANCE_KIND[r.kind]} · <span className="font-mono">{r.id}</span>
                          </span>
                        </th>
                        <td className="py-1.5 pr-3 text-text-muted">{r.detail}</td>
                        <td className="py-1.5">
                          <SourceChips ids={r.source_ids} index={index} className="ml-0" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs">
              <Link href="/config/strategy" className={LINK}>
                Rules, hard limits and bands in Config: Strategy
              </Link>
            </p>
          </section>

          {body.holding_periods && body.holding_periods.length > 0 ? (
            <section aria-labelledby="review-holding-heading" className={SECTION}>
              <BlueprintCorners />
              <h2 id="review-holding-heading" className="text-[22px] leading-tight font-semibold">
                Holding periods
              </h2>
              <p className="text-sm text-text-muted">
                Each open purchase lot, with the date it passes the holding-period test you set. Dates and your own thresholds, not tax advice.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">Open lots and the date each passes the holding-period test</caption>
                  <thead>
                    <tr className="border-b border-border text-text-muted">
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Holding</th>
                      <th scope="col" className="py-1.5 pr-3 text-right font-medium">Bought</th>
                      <th scope="col" className="py-1.5 pr-3 text-right font-medium">Passes the test</th>
                      <th scope="col" className="py-1.5 text-left font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {body.holding_periods.map((h) => (
                      <tr key={`${h.ticker}-${h.bought_on}`} className="border-b border-border-soft">
                        <th scope="row" className="py-1.5 pr-3 text-left font-normal">{h.ticker}</th>
                        <td className="num py-1.5 pr-3 text-right">{formatDate(h.bought_on)}</td>
                        <td className="num py-1.5 pr-3 text-right">{formatDate(h.passes_on)}</td>
                        <td className="py-1.5">
                          {h.passed ? "Passed" : `${h.months_remaining.toFixed(1)} months left`}
                          {h.forfeits_exemption_warning ? <span className="ml-2 font-medium">Sell target reached: a full sale would give up the exemption</span> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs">
                <Link href="/performance/lots" className={LINK}>
                  Lots and holding periods
                </Link>
              </p>
            </section>
          ) : null}

          <section aria-labelledby="review-kpis-heading" className={SECTION}>
            <BlueprintCorners />
            <h2 id="review-kpis-heading" className="text-[22px] leading-tight font-semibold">
              Thesis KPIs
            </h2>
            {body.kpis.length === 0 ? (
              <p className="text-sm text-text-muted">No thesis KPIs to report.</p>
            ) : (
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[34rem] text-sm">
                  <thead>
                    <tr className="border-b border-border text-text-muted">
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Status</th>
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Holding</th>
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">KPI</th>
                      <th scope="col" className="py-1.5 pr-3 text-left font-medium">Period</th>
                      <th scope="col" className="py-1.5 text-left font-medium">Sources</th>
                    </tr>
                  </thead>
                  <tbody>
                    {body.kpis.map((k) => (
                      <tr key={k.ref} id={kpiAnchor(k.ref)} className="scroll-mt-24 border-b border-border align-top last:border-0 target:bg-surface-muted">
                        <td className="py-1.5 pr-3">
                          <VerdictStatusBadge status={k.status} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <Link href={`/holdings/${encodeURIComponent(k.ticker)}`} className="font-mono text-xs text-accent-ink hover:underline">
                            {k.ticker}
                          </Link>
                        </td>
                        <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                          {k.description} <span className="block font-mono text-xs text-text-muted">{k.kpi_id}</span>
                        </th>
                        <td className="num py-1.5 pr-3">{k.period ?? <span className="text-text-muted">never judged</span>}</td>
                        <td className="py-1.5">
                          <SourceChips ids={k.source_ids} index={index} className="ml-0" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {body.reviews_due.length > 0 ? (
              <p className="text-sm">
                Theses due for review:{" "}
                {body.reviews_due.map((t, i) => (
                  <span key={t}>
                    {i > 0 ? ", " : ""}
                    <Link href={`/config/theses#${encodeURIComponent(t)}`} className={LINK}>
                      {t}
                    </Link>
                  </span>
                ))}
              </p>
            ) : null}
          </section>

          <section aria-labelledby="review-flags-heading" className={SECTION}>
            <BlueprintCorners />
            <h2 id="review-flags-heading" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
              <FlagIcon aria-hidden="true" className="size-4 text-text-muted" />
              Flags
            </h2>
            {body.flags.length === 0 ? <p className="text-sm text-text-muted">Nothing flagged.</p> : null}
            <ul className="space-y-1 text-sm">
              {body.flags.map((f, i) => (
                <li key={i} className="max-w-prose">
                  {f.text}
                  <SourceChips ids={f.source_ids} index={index} />
                </li>
              ))}
            </ul>
          </section>

          {(body.risk_notes ?? []).length > 0 ? (
            <section aria-labelledby="review-risk-heading" className={SECTION}>
              <BlueprintCorners />
              <h2 id="review-risk-heading" className="text-[22px] leading-tight font-semibold">
                Portfolio risk
              </h2>
              <ul className="space-y-1 text-sm">
                {(body.risk_notes ?? []).map((line, i) => (
                  <li key={i} className="max-w-prose">{line}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {(body.bench_notes ?? []).length > 0 ? (
            <section aria-labelledby="review-bench-heading" className={SECTION}>
              <BlueprintCorners />
              <h2 id="review-bench-heading" className="text-[22px] leading-tight font-semibold">
                Bench
              </h2>
              <ul className="space-y-1 text-sm">
                {(body.bench_notes ?? []).map((line, i) => (
                  <li key={i} className="max-w-prose">{line}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <ActionsSection report={report} />
        </article>

        <SourceSidebar index={index} />
      </div>
    </div>
  );
}

function RuleCoverage({ strategy, missing, ruleCount }: { strategy: StrategyLoad; missing: { id: string; description: string }[]; ruleCount: number | null }) {
  if (strategy.kind === "loading") {
    return <p className="text-xs text-text-muted">Checking that every strategy rule is listed…</p>;
  }
  if (strategy.kind === "error") {
    return (
      <p className="flex items-center gap-1.5 text-xs text-text">
        <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
        Could not load the strategy to check that every rule is listed: {strategy.message}
      </p>
    );
  }
  const against = strategy.version !== null ? `strategy version #${strategy.version}${strategy.current ? " (the active one)" : ""}` : "the strategy";
  if (missing.length > 0) {
    return (
      <AlertMessage title={`${missing.length} strategy rule${missing.length === 1 ? " is" : "s are"} missing from this table`}>
        <p>
          Checked against {against}
          {strategy.current ? "; the strategy may have changed since the review was written" : ""}. Not judged:
        </p>
        <ul className="list-disc pl-5">
          {missing.map((m) => (
            <li key={m.id}>
              <span className="font-mono text-xs">{m.id}</span> {m.description}
            </li>
          ))}
        </ul>
      </AlertMessage>
    );
  }
  return (
    <p className="flex items-center gap-1.5 text-xs text-text-muted">
      <ShieldCheckIcon aria-hidden="true" className="size-3.5 text-neutral-signal" />
      Every rule of {against} is in the table ({ruleCount ?? 0} rule{ruleCount === 1 ? "" : "s"}).
    </p>
  );
}

function ActionsSection({ report }: { report: ReviewReportOut }) {
  const { body } = report;
  return (
    <section aria-labelledby="review-actions-heading" className={SECTION}>
            <BlueprintCorners />
      <h2 id="review-actions-heading" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
        <LightbulbIcon aria-hidden="true" className="size-4 text-text-muted" />
        Proposed actions
      </h2>
      <p className="text-xs text-text-muted">
        Proposed by the model; each rests on the rules or KPIs it names. Nothing is traded or changed by the review.
        {body.dropped_actions > 0
          ? ` ${body.dropped_actions} proposed action${body.dropped_actions === 1 ? " was" : "s were"} dropped for citing no valid rule or KPI.`
          : ""}
      </p>
      {body.actions.length === 0 ? <p className="text-sm text-text-muted">No action proposed.</p> : null}
      <ol className="space-y-3">
        {body.actions.map((a, i) => {
          const basis = actionBasis(a, body);
          return (
            <li key={i} className="space-y-1 border border-border-soft p-3 text-sm">
              <p className="font-medium">
                {a.action}
                {a.ticker ? (
                  <>
                    {" "}
                    <Link href={`/holdings/${encodeURIComponent(a.ticker)}`} className="font-mono text-xs font-normal text-accent-ink hover:underline">
                      {a.ticker}
                    </Link>
                  </>
                ) : null}
              </p>
              <p className="text-text-muted">{a.rationale}</p>
              {basis.rules.length > 0 ? (
                <p className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-text-muted">Rules:</span>
                  {basis.rules.map((r) =>
                    r.href ? (
                      <a key={r.id} href={r.href} className="inline-flex items-center gap-1 rounded-sm border border-border px-1 hover:border-accent">
                        <span className="font-mono">{r.id}</span>
                        {r.row ? <span className="text-text-muted">({COMPLIANCE_STATUS[r.row.status].label.toLowerCase()})</span> : null}
                      </a>
                    ) : (
                      <span key={r.id} className="rounded-sm border border-dashed border-border px-1 text-text-muted">
                        <span className="font-mono">{r.id}</span> (not in this review)
                      </span>
                    ),
                  )}
                </p>
              ) : null}
              {basis.kpis.length > 0 ? (
                <p className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-text-muted">KPIs:</span>
                  {basis.kpis.map((k) => (
                    <span key={k.ref} className="inline-flex items-center gap-1">
                      {k.href ? (
                        <a href={k.href} className="rounded-sm border border-border px-1 font-mono hover:border-accent">
                          {k.ref}
                          {k.row ? <span className="font-sans text-text-muted"> ({COMPLIANCE_STATUS[k.row.status].label.toLowerCase()})</span> : null}
                        </a>
                      ) : (
                        <span className="rounded-sm border border-dashed border-border px-1 font-mono text-text-muted">{k.ref} (not in this review)</span>
                      )}
                      {k.holdingHref ? (
                        <Link href={k.holdingHref} className="text-accent-ink hover:underline">
                          holding
                        </Link>
                      ) : null}
                    </span>
                  ))}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
