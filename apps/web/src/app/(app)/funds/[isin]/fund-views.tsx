"use client";

import { DownloadIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { useFundProfile } from "@/components/fund/fund-shell";
import { Absent, ResourceGate, SourceLink, Stat } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatFigure, toNumber } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import {
  fundHref,
  money,
  pct,
  type BondView,
  type FundBucket,
  type FundProfile,
  type HoldingsView,
  type ProfileField,
  type RateBucket,
  type StyleView,
} from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import { RecordFactsForm } from "./record-facts-form";

export { ChecklistTab } from "./checklist-view";
export { PerformanceTab } from "./performance-view";

/** Weights as bars: the unknown or unclassified share is drawn dashed so it cannot pass for a holding. */
function WeightBars({ items, label }: { items: readonly FundBucket[]; label: string }) {
  const max = Math.max(...items.map((i) => toNumber(i.weight) ?? 0), 0.0001);
  return (
    <ul aria-label={label} className="space-y-1.5 text-sm">
      {items.map((b) => {
        const w = toNumber(b.weight) ?? 0;
        const gap = b.kind !== "known";
        return (
          <li key={b.label} className="grid grid-cols-[minmax(7rem,13rem)_1fr_4rem] items-center gap-3">
            <span className={cn("truncate", gap && "italic text-text-muted")} title={b.label}>
              {b.label}
            </span>
            <span className="h-2 bg-border-soft" aria-hidden="true">
              <span className={cn("block h-full", gap ? "border border-dashed border-text-faint bg-transparent" : "bg-chart-line")} style={{ width: `${Math.max(1, (w / max) * 100)}%` }} />
            </span>
            <span className="num text-right text-xs">{pct(b.weight, 1)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function FieldValue({ f }: { f: ProfileField }) {
  if (f.value === null || f.value === undefined) return <Absent reason={f.reason ? `missing: ${f.reason}` : "missing: not stored"} />;
  let text: ReactNode = f.value;
  if (f.unit === "fraction") text = pct(f.value) ?? f.value;
  else if (f.unit === "money") text = money(f.value, f.currency) ?? f.value;
  else if (f.unit === "date") text = formatDate(f.value);
  return (
    <span>
      <span className={f.unit === "text" ? "" : "num"}>{text}</span>
      {f.reason ? <span className="block text-xs text-text-muted">{f.reason}</span> : null}
    </span>
  );
}

export function ProfileView({ isin }: { isin: string }) {
  const state = useFundProfile();
  return (
    <ResourceGate state={state} label="the fund profile">
      {(p: FundProfile) => (
        <div className="flex flex-col gap-5">
          {(p.warnings ?? []).map((w) => (
            <AlertMessage key={w} title="Note">{w}</AlertMessage>
          ))}
          <Blueprint aria-labelledby="fp-facts" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Issuer data, each with its source" title={<span id="fp-facts">What the fund is</span>} />
            <dl className="grid gap-x-10 sm:grid-cols-2">
              {p.fields.map((f) => (
                <div key={f.key} className="flex gap-3 border-b border-border-soft py-1.5 text-sm">
                  <dt className="w-36 shrink-0 text-text-muted">{f.label}</dt>
                  <dd className="min-w-0">
                    <FieldValue f={f} />
                    {f.value && f.source ? (
                      <span className="block text-[11px] text-text-muted">
                        {f.source}
                        {f.as_of ? `, ${formatDate(f.as_of)}` : ""} {f.source_url ? <SourceLink href={f.source_url}>source</SourceLink> : null}
                      </span>
                    ) : null}
                  </dd>
                </div>
              ))}
            </dl>
            {Object.keys(p.charges ?? {}).length > 0 ? (
              <p className="text-xs text-text-muted">
                Charges as published: {Object.entries(p.charges ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "number" ? pct(v) : String(v)}`).join(", ")}.
              </p>
            ) : null}
            <p className="text-xs text-text-muted">
              Missing fields can be recorded with a source on the{" "}
              <Link href={fundHref(isin, "checklist")} className="text-accent-ink hover:underline">
                due diligence tab
              </Link>
              .
            </p>
          </Blueprint>
          <Blueprint aria-labelledby="fp-identity" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Fund, share class and listing are different things" title={<span id="fp-identity">Identity</span>} />
            <p className="max-w-[72ch] text-sm text-text-muted">{p.identity.note}</p>
            {p.identity.sibling_share_classes.length > 0 ? (
              <div className="text-sm">
                <p className="mb-1 text-xs text-text-muted">Other share classes of the same portfolio (not added to this one)</p>
                <ul className="space-y-0.5">
                  {p.identity.sibling_share_classes.map((s) => (
                    <li key={s.isin}>
                      <Link href={fundHref(s.isin)} className="font-mono text-xs text-accent-ink hover:underline">
                        {s.isin}
                      </Link>{" "}
                      {s.name}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-text-muted">No other share class of this portfolio is stored.</p>
            )}
          </Blueprint>
        </div>
      )}
    </ResourceGate>
  );
}

export function HoldingsTab({ isin }: { isin: string }) {
  const [top, setTop] = useState(25);
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/holdings", { params: { path: { isin }, query: { top } } }), [isin, top]);
  const { state } = useApiResource(fetcher);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setError(null);
    const { data, error: err } = await getBrowserApiClient().GET("/funds/{isin}/holdings.csv", { params: { path: { isin } }, parseAs: "text" });
    if (err || typeof data !== "string") {
      setError("The export could not be created. A fund without a holdings snapshot has nothing to export.");
      return;
    }
    const url = URL.createObjectURL(new Blob([data], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${isin}-derived-exposure.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <ResourceGate state={state} label="holdings">
      {(v: HoldingsView) => (
        <div className="flex flex-col gap-5">
          <Blueprint aria-labelledby="fh-top" className="space-y-4 px-[22px] py-5">
            <PanelHeading
              kicker={v.as_of ? `Issuer holdings as of ${formatDate(v.as_of)} · ${v.source}` : "No holdings stored"}
              title={<span id="fh-top">Largest holdings by issuer</span>}
              action={
                <button type="button" onClick={download} disabled={!v.as_of} className="inline-flex items-center gap-1.5 border border-border px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50">
                  <DownloadIcon aria-hidden="true" className="size-4" />
                  Derived exposure CSV
                </button>
              }
            />
            {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
            {v.warnings.map((w) => (
              <AlertMessage key={w} title="Note">{w}</AlertMessage>
            ))}
            {!v.as_of ? (
              <p className="text-sm text-text-muted">No holdings snapshot is stored for this fund, so its holdings are unknown. They are not empty.</p>
            ) : (
              <>
                <p className={cn("text-sm", v.reconciles === false ? "text-unconfirmed" : "text-text-muted")}>{v.reconcile_note}</p>
                <div className="overflow-x-auto border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-surface-muted text-left">
                        <th scope="col" className="px-3 py-1.5 font-medium">Issuer</th>
                        <th scope="col" className="px-2 py-1.5 text-right font-medium">Weight</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">Sector</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">Country</th>
                        <th scope="col" className="px-2 py-1.5 text-right font-medium">Rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {v.top.map((l) => (
                        <tr key={l.issuer_key} className="border-t border-border-soft">
                          <th scope="row" className="px-3 py-1 text-left font-normal">
                            {l.label}
                            {l.isins.length > 0 ? <span className="block font-mono text-[11px] text-text-muted">{l.isins.join(", ")}</span> : <span className="block text-[11px] text-unconfirmed">no ISIN, matched by name only</span>}
                          </th>
                          <td className="num px-2 py-1 text-right">{pct(l.weight)}</td>
                          <td className="px-2 py-1">{l.sector ?? <Absent reason="missing: no sector published" />}</td>
                          <td className="px-2 py-1">{l.country ?? <Absent reason="missing: no country published" />}</td>
                          <td className="num px-2 py-1 text-right">{l.rows}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-text-muted">{v.issuer_count} issuers.</span>
                  {top < 200 && v.issuer_count > top ? (
                    <button type="button" onClick={() => setTop((t) => Math.min(200, t * 2))} className="text-accent-ink hover:underline">
                      Show more
                    </button>
                  ) : null}
                </div>
              </>
            )}
          </Blueprint>
          {v.as_of ? (
            <div className="grid gap-5 lg:grid-cols-2">
              <Blueprint aria-labelledby="fh-asset" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="Share of net assets" title={<span id="fh-asset">Asset classes</span>} />
                <WeightBars items={v.asset_classes} label="Asset classes" />
                <p className="text-xs text-text-muted">The dashed bar is weight the published rows do not account for. It is unknown and is not assigned to any holding.</p>
              </Blueprint>
              <Blueprint aria-labelledby="fh-cur" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="All published rows" title={<span id="fh-cur">Currency</span>} />
                <WeightBars items={v.currencies} label="Currencies" />
              </Blueprint>
              <Blueprint aria-labelledby="fh-sec" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="Equity rows" title={<span id="fh-sec">Sector</span>} />
                <WeightBars items={v.sectors} label="Sectors" />
              </Blueprint>
              <Blueprint aria-labelledby="fh-ctry" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="Equity rows" title={<span id="fh-ctry">Country</span>} />
                <WeightBars items={v.countries} label="Countries" />
              </Blueprint>
            </div>
          ) : null}
          {v.publisher_breakdowns.map((b) => (
            <Blueprint key={`${b.dimension}-${b.scheme}`} className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker={`Published by the issuer${b.as_of ? `, ${formatDate(b.as_of)}` : ""}`} title={`${b.dimension} (${b.scheme.replace(/_/g, " ")}) against the fund's index`} />
              <div className="overflow-x-auto border border-border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-surface-muted text-left">
                      <th scope="col" className="px-3 py-1.5 font-medium">Group</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Fund</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Index</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.slice(0, 15).map((r) => (
                      <tr key={r.label} className="border-t border-border-soft">
                        <th scope="row" className="px-3 py-1 text-left font-normal">{r.label}</th>
                        <td className="num px-2 py-1 text-right">{pct(r.fund_weight) ?? <Absent reason="missing: not published" />}</td>
                        <td className="num px-2 py-1 text-right">{pct(r.benchmark_weight) ?? <Absent reason="missing: not published" />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Blueprint>
          ))}
        </div>
      )}
    </ResourceGate>
  );
}

export function StyleTab({ isin }: { isin: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/style", { params: { path: { isin } } }), [isin]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="concentration and style">
      {(v: StyleView) => (
        <div className="flex flex-col gap-5">
          <AlertMessage title="StewardAI proxies, not a style box">{v.note}</AlertMessage>
          {v.warnings.map((w) => (
            <AlertMessage key={w} title="Coverage">{w}</AlertMessage>
          ))}
          <Blueprint aria-labelledby="fs-conc" className="space-y-4 px-[22px] py-5">
            <PanelHeading kicker={v.as_of ? `Holdings as of ${formatDate(v.as_of)}` : "No holdings stored"} title={<span id="fs-conc">Concentration</span>} />
            {v.concentration ? (
              <>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
                  <Stat label="Top 10 issuers" note="share of net assets">{pct(v.concentration.top10_weight, 1) ?? <Absent />}</Stat>
                  <Stat label="Top 10 of equity" note="share of identified equity">{pct(v.concentration.top10_of_equity, 1) ?? <Absent />}</Stat>
                  <Stat label="Effective issuers" note="1 / HHI">{formatFigure(v.concentration.effective_issuers, "per_share") ?? <Absent />}</Stat>
                  <Stat label="Issuers" note={`${pct(v.concentration.equity_weight, 1)} of net assets is identified equity`}>{v.concentration.issuers}</Stat>
                </dl>
                <p className="text-xs text-text-muted">{v.concentration.basis}.</p>
              </>
            ) : (
              <p className="text-sm text-text-muted">No identified equity holdings, so concentration is unknown.</p>
            )}
          </Blueprint>
          <Blueprint aria-labelledby="fs-vg" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Rule-based, from holdings the app can value" title={<span id="fs-vg">Value or growth tilt</span>} />
            {(v.value_growth ?? []).length === 0 ? (
              <p className="text-sm text-text-muted">Not available without identified equity holdings.</p>
            ) : (
              <>
                <WeightBars items={(v.value_growth ?? []).map((s) => ({ label: s.label, weight: s.weight, kind: s.kind === "no_earnings" || s.kind === "not_covered" ? "unclassified" : "known" }))} label="Value or growth tilt" />
                <p className="max-w-[72ch] text-xs text-text-muted">{v.value_growth_rule}</p>
              </>
            )}
          </Blueprint>
          <div className="grid gap-5 lg:grid-cols-2">
            <Blueprint aria-labelledby="fs-size" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Market capitalisation in EUR, ECB rates" title={<span id="fs-size">Size mix</span>} />
              {v.size.length === 0 ? (
                <p className="text-sm text-text-muted">Not available without identified equity holdings.</p>
              ) : (
                <>
                  <WeightBars items={v.size.map((s) => ({ label: s.label, weight: s.weight, kind: s.kind === "known" ? "known" : "unclassified" }))} label="Size mix" />
                  <p className="text-xs text-text-muted">
                    Market cap is known for {pct(v.size_coverage, 0) ?? "an unknown share"} of the identified equity weight; the dashed bar is the rest.
                  </p>
                </>
              )}
            </Blueprint>
            <Blueprint aria-labelledby="fs-val" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="Covered holdings only" title={<span id="fs-val">Valuation and profitability</span>} />
              {v.metrics.length === 0 ? (
                <p className="text-sm text-text-muted">Not available without identified equity holdings.</p>
              ) : (
                <ul className="space-y-3 text-sm">
                  {v.metrics.map((m) => {
                    const covered = toNumber(m.covered_weight) ?? 0;
                    const eq = toNumber(m.equity_weight) ?? 0;
                    return (
                      <li key={m.id} className="border-b border-border-soft pb-2">
                        <div className="flex items-baseline justify-between gap-3">
                          <span>{m.label}</span>
                          <span className="num text-base font-semibold">
                            {m.value === null || m.value === undefined ? <Absent reason={m.reason ?? "missing: not computable"} /> : m.unit === "fraction" ? pct(m.value) : `${formatFigure(m.value, "per_share")}×`}
                          </span>
                        </div>
                        <p className="text-xs text-text-muted">
                          Rests on {eq > 0 ? `${((covered / eq) * 100).toFixed(0)}%` : "0%"} of identified equity weight; {m.excluded} holdings left out. {m.formula}.
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Blueprint>
          </div>
        </div>
      )}
    </ResourceGate>
  );
}

/** Where a maturity or duration figure came from, so a figure you recorded is never mistaken for a computed one. */
function resultOrigin(r: BondView["duration"]): string | undefined {
  if (r.value === null || r.value === undefined) return undefined;
  if ((r.sources ?? []).some((s) => s.kind === "owner_input")) return "Recorded by you, from the source below";
  if ((r.warnings ?? []).some((w) => w.startsWith("DV01-implied"))) return "Implied from the filing's DV01, not stated by the issuer";
  return undefined;
}

function resultText(r: BondView["duration"]): string {
  const n = toNumber(r.value);
  if (n === null) return String(r.value);
  const unit = r.unit && r.unit !== "number" ? ` ${r.unit}` : "";
  return `${n.toFixed(2)}${unit}`;
}

function sensitivity(v: number | string | null | undefined): string | null {
  const n = toNumber(v);
  return n === null ? null : n.toLocaleString("en-GB", { maximumFractionDigits: 2 });
}

function RateBucketBars({ buckets }: { buckets: readonly RateBucket[] }) {
  const max = Math.max(...buckets.map((b) => Math.abs(toNumber(b.years) ?? 0)), 0.0001);
  return (
    <ul aria-label="Duration by key rate" className="space-y-1.5 text-sm">
      {buckets.map((b) => {
        const y = toNumber(b.years);
        return (
          <li key={b.label} className="grid grid-cols-[minmax(4rem,7rem)_1fr_4.5rem] items-center gap-3">
            <span>{b.label}</span>
            <span className="h-2 bg-border-soft" aria-hidden="true">
              <span className="block h-full bg-chart-line" style={{ width: `${y === null ? 0 : Math.max(1, (Math.abs(y) / max) * 100)}%` }} />
            </span>
            <span className="num text-right text-xs">{y === null ? <Absent reason="missing: not reported" /> : `${y.toFixed(2)} yr`}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function BondTab({ isin }: { isin: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/bond", { params: { path: { isin } } }), [isin]);
  const { state, reload } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="bond characteristics">
      {(v: BondView) => (
        <div className="flex flex-col gap-5">
          <Blueprint aria-labelledby="fb-main" className="space-y-4 px-[22px] py-5">
            <PanelHeading kicker={v.applicable === true ? "Looks like a bond or multi-asset fund" : v.applicable === false ? "Not mainly a bond fund" : "Fund type unknown"} title={<span id="fb-main">Maturity, duration and credit quality</span>} />
            <p className="max-w-[72ch] text-sm text-text-muted">{v.applicability_reason}</p>
            <dl className="grid gap-4 sm:grid-cols-3">
              {([["Weighted maturity", v.weighted_maturity], ["Duration", v.duration], ["Credit quality", v.credit_quality]] as const).map(([label, r]) => (
                <Stat key={label} label={label} note={resultOrigin(r)}>
                  {r.value === null || r.value === undefined ? <Absent reason="missing: neither the stored holdings nor a fact you recorded carry it" /> : resultText(r)}
                </Stat>
              ))}
            </dl>
            <ul className="space-y-1 text-xs text-text-muted">
              {([["Weighted maturity", v.weighted_maturity], ["Duration", v.duration], ["Credit quality", v.credit_quality]] as const).flatMap(([label, r]) => [
                ...(r.warnings ?? []).map((w) => <li key={`${label}-${w}`}>{label}: {w}</li>),
                ...(r.sources ?? []).map((s) => (
                  <li key={`${label}-${s.kind}-${s.ref}`}>
                    {label} source: {s.kind === "owner_input" ? "recorded by you" : s.kind.replace(/_/g, " ")}, {s.ref}
                    {s.url ? <> <SourceLink href={s.url}>document</SourceLink></> : null}
                  </li>
                )),
              ])}
            </ul>
          </Blueprint>
          <div className="grid gap-5 lg:grid-cols-2">
            <Blueprint aria-labelledby="fb-stated" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker="As the issuer states it" title={<span id="fb-stated">Factsheet characteristics</span>} />
              <dl className="space-y-1.5 text-sm">
                {v.stated.map((f) => (
                  <div key={f.key} className="flex gap-3 border-b border-border-soft py-1">
                    <dt className="w-36 shrink-0 text-text-muted">{f.label}</dt>
                    <dd>{f.value ?? <Absent reason={f.reason ? `missing: ${f.reason}` : "missing: not stored"} />}</dd>
                  </div>
                ))}
              </dl>
            </Blueprint>
            <Blueprint aria-labelledby="fb-mix" className="space-y-3 px-[22px] py-5">
              <PanelHeading kicker={v.asset_mix_source ?? "No regulatory filing stored"} title={<span id="fb-mix">Instrument categories</span>} />
              {v.asset_mix.length === 0 ? (
                <p className="text-sm text-text-muted">No instrument-category mix is stored. Only US-registered funds file one.</p>
              ) : (
                <WeightBars items={v.asset_mix.map((m) => ({ label: m.label, weight: m.weight, kind: "known" as const }))} label="Instrument categories" />
              )}
            </Blueprint>
          </div>
          {(v.rate_buckets ?? []).length > 0 || (v.credit_spread ?? []).length > 0 ? (
            <div className="grid gap-5 lg:grid-cols-2">
              {(v.rate_buckets ?? []).length > 0 ? (
                <Blueprint aria-labelledby="fb-rate" className="space-y-3 px-[22px] py-5">
                  <PanelHeading kicker="Duration contributed by each key rate, from the filing's DV01" title={<span id="fb-rate">Interest-rate sensitivity</span>} />
                  <RateBucketBars buckets={v.rate_buckets ?? []} />
                  <p className="text-xs text-text-muted">Each bar is the part of the duration, in years, that comes from that point on the yield curve. Together they make the DV01-implied duration.</p>
                </Blueprint>
              ) : null}
              {(v.credit_spread ?? []).length > 0 ? (
                <Blueprint aria-labelledby="fb-spread" className="space-y-3 px-[22px] py-5">
                  <PanelHeading kicker="A sensitivity, not a share of holdings" title={<span id="fb-spread">Credit-spread sensitivity</span>} />
                  <div className="overflow-x-auto border border-border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-surface-muted text-right">
                          <th scope="col" className="px-3 py-1.5 text-left font-medium">Spread move</th>
                          <th scope="col" className="px-2 py-1.5 font-medium">Investment grade</th>
                          <th scope="col" className="px-2 py-1.5 font-medium">Below investment grade</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(v.credit_spread ?? []).map((s) => (
                          <tr key={s.label} className="border-t border-border-soft">
                            <th scope="row" className="px-3 py-1 text-left font-normal">{s.label}</th>
                            <td className="num px-2 py-1 text-right">{sensitivity(s.investment_grade) ?? <Absent reason="missing: not reported in the filing" />}</td>
                            <td className="num px-2 py-1 text-right">{sensitivity(s.non_investment_grade) ?? <Absent reason="missing: not reported in the filing" />}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-xs text-text-muted">These figures show how much value the filing says the portfolio moves for a change in credit spreads, in the filing&apos;s own units. They do not say how much of the fund is investment grade.</p>
                </Blueprint>
              ) : null}
            </div>
          ) : null}
          {v.credit_note ? (
            <Blueprint aria-labelledby="fb-note" className="space-y-2 px-[22px] py-5">
              <PanelHeading kicker="Recorded by you, with its source" title={<span id="fb-note">Credit quality note</span>} />
              <p className="max-w-[72ch] text-sm">{v.credit_note}</p>
            </Blueprint>
          ) : null}
          <Blueprint aria-labelledby="fb-record" className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Not in the stored data; you record it with a source" title={<span id="fb-record">Record maturity, duration or credit quality</span>} />
            <RecordFactsForm
              isin={isin}
              submitLabel="Record"
              onSaved={reload}
              fields={[
                { key: "average_maturity_years", hint: "In years, zero or more" },
                { key: "effective_duration_years", hint: "In years, zero or more" },
                { key: "credit_quality_note" },
              ]}
            />
            <p className="text-xs text-text-muted">A figure you record is shown with its source and replaces a DV01-implied duration. Fill in only what the document states.</p>
          </Blueprint>
          {v.warnings.map((w) => (
            <p key={w} className="text-xs text-text-muted">{w}</p>
          ))}
        </div>
      )}
    </ResourceGate>
  );
}
