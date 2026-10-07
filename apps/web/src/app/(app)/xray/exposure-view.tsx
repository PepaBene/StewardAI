"use client";

import { TriangleAlertIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { CsvDownload } from "@/components/ui/csv-download";
import { CompositionStrip } from "@/components/xray/composition-strip";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";
import {
  COUNTRY_KIND_LABEL,
  compositionSegments,
  groupViews,
  segmentTone,
  type XrayBucket,
  type XrayData,
  type XrayView,
} from "@/lib/xray/xray";

import { BasisChips, Fact, Notes, Pct, TD, TH, TH_LEFT, Warnings, WeightBar } from "./shared";

const fetchXray = () => getBrowserApiClient().GET("/portfolio/xray");

export function ExposureView() {
  const { state } = useApiResource(fetchXray);
  return (
    <ResourceGate state={state} label="X-Ray">
      {(data) => <Exposure data={data} />}
    </ResourceGate>
  );
}

function Coverage({ data }: { data: XrayData }) {
  const currency = data.base_currency ?? "";
  const unknown = data.unknown?.unknown_fund_weight ?? 0;
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="What this is built from" title="Your holdings and each fund's published holdings" />
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
        <Fact label="Portfolio value" note={data.snapshot_date ? `Snapshot ${formatDate(data.snapshot_date)}` : undefined}>
          {currency ? formatMoney(data.total_value, currency, { decimals: 0 }) : "—"}
        </Fact>
        <Fact label="Unknown fund weight" note="Hatched in every view, never spread over known holdings">
          <Pct value={unknown} />
        </Fact>
        <Fact label="Issuers scored" note="For size, style and fundamentals">
          <span className="num">
            {data.scored_issuers} of {data.issuers_total}
          </span>
        </Fact>
        <Fact label="Funds opened up">
          <span className="num">{data.funds.filter((f) => f.status === "ok" || f.status === "stale").length}</span>
          <span className="text-sm font-normal text-text-muted"> of {data.funds.length}</span>
        </Fact>
      </dl>
      {data.funds.length > 0 ? (
        <div className="mt-4 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Each held fund, the date of its published holdings and how much of it is unknown</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Fund</th>
                <th scope="col" className={TH}>Weight</th>
                <th scope="col" className={TH_LEFT}>Holdings as of</th>
                <th scope="col" className={TH}>Unknown part</th>
              </tr>
            </thead>
            <tbody>
              {data.funds.map((f) => (
                <tr key={f.isin} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {f.label}
                    {f.warnings.length > 0 ? <span className="block text-[11px] text-text-muted">{f.warnings[0]}</span> : null}
                  </th>
                  <td className={TD}>{formatPercent(f.weight, { decimals: 1 })}</td>
                  <td className="px-3 py-1.5">
                    {f.holdings_as_of ? (
                      <span className="num">
                        {formatDate(f.holdings_as_of)}
                        {f.stale ? <span className="ml-2 text-xs font-medium text-warning-signal">stale, {f.lag_days} days old</span> : null}
                      </span>
                    ) : (
                      <span className="text-text-muted">
                        {f.status === "not_configured" ? "Not set up for look-through" : "No holdings stored yet"}
                      </span>
                    )}
                  </td>
                  <td className={TD}>{formatPercent(f.unknown_weight, { decimals: 1 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Blueprint>
  );
}

function LensRail({ views, active }: { views: XrayView[]; active: string }) {
  return (
    <nav aria-label="Lenses" className="lg:sticky lg:top-4 lg:self-start">
      <div className="space-y-3">
        {groupViews(views).map((g) => (
          <div key={g.group}>
            <p className="kicker">{g.label}</p>
            <ul className="mt-1.5 flex flex-wrap gap-1.5 lg:flex-col lg:gap-0">
              {g.views.map((v) => (
                <li key={v.dimension}>
                  <Link
                    href={`/xray?lens=${v.dimension}`}
                    scroll={false}
                    aria-current={v.dimension === active ? "true" : undefined}
                    className={cn(
                      "block border px-2.5 py-1.5 text-sm transition-colors lg:border-0 lg:border-l-2",
                      v.dimension === active
                        ? "border-accent bg-accent-soft font-semibold text-text"
                        : "border-border-soft text-text-muted hover:text-text lg:border-border-soft",
                    )}
                  >
                    {v.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

function Contributors({ bucket }: { bucket: XrayBucket }) {
  if (bucket.contributors.length === 0) return <span className="text-text-faint">—</span>;
  const shown = bucket.contributors.slice(0, 3);
  const rest = bucket.contributors.slice(3);
  return (
    <p className="text-[13px] leading-snug">
      {shown.map((c, i) => (
        <span key={c.key}>
          {i > 0 ? ", " : ""}
          {c.label} <span className="num text-text-muted">{formatPercent(c.weight, { decimals: 1 })}</span>
        </span>
      ))}
      {rest.length > 0 ? (
        <span className="text-text-muted" title={rest.map((c) => `${c.label} ${formatPercent(c.weight, { decimals: 1 })}`).join(", ")}>
          {" "}
          and {rest.length} more
        </span>
      ) : null}
    </p>
  );
}

function BucketTable({ view, currency, total }: { view: XrayView; currency: string; total: number }) {
  const tones = new Map(compositionSegments(view.buckets).map((s) => [s.key, segmentTone(s)]));
  const max = Math.max(...view.buckets.map((b) => b.weight), 0);
  return (
    <div className="overflow-x-auto border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">{view.label}: every bucket with its weight, value, contributors and how it was classified</caption>
        <thead>
          <tr className="bg-surface-muted">
            <th scope="col" className={TH_LEFT}>{view.label}</th>
            <th scope="col" className={TH}>Weight</th>
            <th scope="col" className={TH}>Value</th>
            <th scope="col" className={TH_LEFT}>Largest contributors</th>
            <th scope="col" className={TH_LEFT}>How it was classified</th>
          </tr>
        </thead>
        <tbody>
          {view.buckets.map((b) => {
            const tone = tones.get(b.key) ?? { className: "bg-neutral-signal/45", hatched: false };
            const unknownish = b.kind === "not_looked_through" || b.kind === "unknown_fund" || b.kind === "unclassified";
            return (
              <tr key={b.key} className="border-t border-border-soft align-top">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  <span className="flex items-start gap-2">
                    <i aria-hidden="true" className={cn("mt-1 inline-block size-2.5 shrink-0 border border-border", tone.className)} />
                    <span>
                      {b.label}
                      {unknownish ? <span className="block text-[11px] text-text-muted">Not known: shown, not estimated</span> : null}
                      {b.fund_weight > 0 && b.direct_weight > 0 ? (
                        <span className="block text-[11px] text-text-muted">
                          direct {formatPercent(b.direct_weight, { decimals: 1 })} · through funds {formatPercent(b.fund_weight, { decimals: 1 })}
                        </span>
                      ) : null}
                    </span>
                  </span>
                </th>
                <td className="px-3 py-1.5 text-right">
                  <WeightBar value={b.weight} max={max} className="ml-auto" />
                </td>
                <td className={TD}>{currency ? formatMoney(b.value_base, currency, { decimals: 0 }) : "—"}</td>
                <td className="px-3 py-1.5">
                  <Contributors bucket={b} />
                </td>
                <td className="px-3 py-1.5">
                  <BasisChips basis={b.basis} />
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-border bg-surface-muted">
            <th scope="row" className="px-3 py-1.5 text-left font-medium">Whole portfolio</th>
            <td className={TD}>{formatPercent(view.total_weight, { decimals: 1 })}</td>
            <td className={TD}>{currency ? formatMoney(view.total_weight * total, currency, { decimals: 0 }) : "—"}</td>
            <td className="px-3 py-1.5 text-xs text-text-muted" colSpan={2}>
              {view.reconciles ? "Adds up to the whole portfolio." : "Does not add up to the whole portfolio: treat this view as unreliable."}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function CountryKinds({ view }: { view: XrayView }) {
  if (!view.country_kinds) return null;
  const rows = Object.entries(view.country_kinds).sort((a, b) => b[1] - a[1]);
  if (rows.length === 0) return null;
  return (
    <div className={cn("border px-3.5 py-3 text-sm", view.mixed_basis ? "border-warning-signal/40 bg-warning-signal-subtle" : "border-border-soft")}>
      <p className="flex items-center gap-2 font-medium">
        {view.mixed_basis ? <TriangleAlertIcon aria-hidden="true" className="size-4 text-warning-signal" /> : null}
        {view.mixed_basis ? "These country labels do not mean the same thing" : "What the country labels mean"}
      </p>
      <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {rows.map(([kind, weight]) => (
          <li key={kind} className="flex justify-between gap-3">
            <span>{COUNTRY_KIND_LABEL[kind] ?? kind}</span>
            <span className="num">{formatPercent(weight, { decimals: 1 })}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FeesBlock({ data }: { data: XrayData }) {
  const fees = data.fees;
  if (!fees) return null;
  return (
    <div className="border border-border-soft px-3.5 py-3 text-sm">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
        <Fact label="Fund fees a year" note="Share of the whole portfolio">
          <Pct value={fees.annual_drag} decimals={2} />
        </Fact>
        <Fact label="In money a year">
          {data.base_currency ? <span className="num">{formatMoney(fees.annual_cost_base, data.base_currency, { decimals: 0 })}</span> : "—"}
        </Fact>
        <Fact label="Average ongoing charge" note="Of the funds whose charge is known">
          <Pct value={fees.weighted_expense_ratio} decimals={2} />
        </Fact>
        <Fact label="Charge unknown" note={fees.funds_without_ratio.join(", ") || "None"}>
          <Pct value={fees.unpriced_weight} />
        </Fact>
      </dl>
      <p className="mt-2 text-xs text-text-muted">{fees.note}</p>
    </div>
  );
}

function Lens({ data, view }: { data: XrayData; view: XrayView }) {
  const currency = data.base_currency ?? "";
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={view.group === "owner data" ? "Needs your data" : view.group}
        title={view.label}
        action={
          <span className="inline-flex flex-wrap items-center gap-3">
            <span className="num text-sm text-text-muted">{formatPercent(view.total_weight, { decimals: 1 })} of the portfolio</span>
            <CsvDownload
              label="Buckets CSV"
              filename={`xray-${view.dimension}.csv`}
              fetchCsv={() =>
                getBrowserApiClient().GET("/portfolio/xray/export", { params: { query: { lens: view.dimension } }, parseAs: "text" })
              }
            />
          </span>
        }
      />
      <p className="mt-1 max-w-3xl text-sm text-text-muted">{view.description}</p>
      <div className="mt-4 space-y-4">
        <Warnings warnings={view.warnings} />
        <CountryKinds view={view} />
        {view.dimension === "fees" ? <FeesBlock data={data} /> : null}
        <CompositionStrip buckets={view.buckets} label={view.label} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
          <span>
            Carries a real label: <span className="num text-text">{formatPercent(view.classified_weight, { decimals: 1 })}</span>
          </span>
          {Object.keys(view.bases).length > 0 ? (
            <span className="inline-flex items-center gap-2">
              Labels are <BasisChips basis={view.bases} />
            </span>
          ) : null}
        </div>
        <BucketTable view={view} currency={currency} total={data.total_value} />
      </div>
    </Blueprint>
  );
}

function Exposure({ data }: { data: XrayData }) {
  const params = useSearchParams();
  const lens = params.get("lens") ?? "sector";
  const view = data.views.find((v) => v.dimension === lens) ?? data.views.find((v) => v.dimension === "sector") ?? data.views[0];
  if (!data.available || !view) {
    return (
      <Blueprint className="px-[22px] py-4">
        <p className="text-sm text-text-muted">No portfolio snapshot is stored yet, so there is nothing to open up. Run a portfolio sync first.</p>
      </Blueprint>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <Coverage data={data} />
      <div className="grid gap-5 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <LensRail views={data.views} active={view.dimension} />
        <div className="min-w-0 space-y-5">
          <Lens data={data} view={view} />
          <Notes notes={[data.method, ...data.notes]} title="How this is built" />
        </div>
      </div>
    </div>
  );
}
