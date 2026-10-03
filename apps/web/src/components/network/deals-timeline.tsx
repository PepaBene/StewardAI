"use client";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { decideEvent } from "@/lib/network/actions";
import { dealStages, dealStateIsProvisional, humanize, termRows, type StageReach } from "@/lib/network/logic";
import type { Deal, DealsPayload, ProForma, RowStatus } from "@/lib/network/types";
import { formatPct } from "@/lib/charts/format";
import { cn } from "@/lib/utils";

import { RowStatusBadge, SourceLink, TierBadge } from "./badges";
import { DecisionControl } from "./decision-control";

const REACH_LABEL: Record<StageReach, string> = { confirmed: "confirmed", suggested: "suggested only", none: "not reached" };

function stageMarker(reach: StageReach): string {
  return reach === "confirmed" ? "bg-accent border border-accent" : reach === "suggested" ? "border border-dashed border-text-muted bg-transparent" : "border border-border bg-transparent";
}

/**
 * Deals and financings on a five-stage track (announcement, amendment, approval, closing,
 * termination). The headline state is flagged as provisional when it rests on suggested events;
 * terms and the dilution/leverage scenario use confirmed events only.
 */
export function DealsTimeline({ ticker, data, asOfDate, onChanged }: { ticker: string; data: DealsPayload; asOfDate: string | null; onChanged: () => void }) {
  const hasDeals = data.deals.length > 0;
  return (
    <Blueprint as="section" aria-labelledby="network-deals-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Deals and financing" title={<span id="network-deals-heading">Transaction timeline</span>} />
      <p className="max-w-prose text-sm text-text">
        {hasDeals
          ? `${data.deals.length} ${data.deals.length === 1 ? "deal or financing is" : "deals and financings are"} tracked; ${data.deals.filter((d) => d.confirmedState !== null).length} with a confirmed state, ${data.deals.filter(dealStateIsProvisional).length} showing a state that rests on suggested events.`
          : "No deal or financing event is stored for this company."}
      </p>
      <ul className="flex flex-col gap-4">
        {data.deals.map((d) => (
          <DealCard key={d.dealKey} ticker={ticker} deal={d} readOnly={asOfDate !== null} onChanged={onChanged} />
        ))}
      </ul>
      {data.limitations.length ? <p className="text-xs text-text-muted">{data.limitations.join(" ")}</p> : null}
      <ProvenanceFooter
        provenance={{
          source: "SEC 8-K/6-K items, merger and proxy documents, issuer releases (company events)",
          asOf: asOfDate,
          knownAt: data.knownAsOf,
          method: "State from confirmed events only is the one analytics rely on; suggested events show where a deal may stand.",
          state: !hasDeals ? "not_covered" : data.deals.every((d) => d.confirmedState !== null && !dealStateIsProvisional(d)) ? "verified" : "partial",
          reason: hasDeals ? null : "No deal events recorded.",
          coverage: hasDeals ? { covered: data.deals.filter((d) => d.confirmedState !== null).length, total: data.deals.length, unit: "deals with a confirmed state" } : null,
        }}
      />
    </Blueprint>
  );
}

function DealCard({ ticker, deal, readOnly, onChanged }: { ticker: string; deal: Deal; readOnly: boolean; onChanged: () => void }) {
  const stages = dealStages(deal);
  const provisional = dealStateIsProvisional(deal);
  const terms = termRows(deal.confirmedTerms, (n) => formatNumber(n, Number.isInteger(n) ? 0 : 2));
  return (
    <li className="flex flex-col gap-3 border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 text-base font-semibold">
          {humanize(deal.eventType)}: {deal.counterpartyKey ?? deal.dealKey}
        </h3>
        <p className="text-xs">
          Confirmed state: <strong>{deal.confirmedState ? humanize(deal.confirmedState) : "none yet"}</strong>
          {provisional ? <span className="ml-2 border border-dashed border-neutral-signal px-1.5 py-0.5">Shown: {deal.state ? humanize(deal.state) : "unknown"} (suggested, not confirmed)</span> : null}
        </p>
      </div>
      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-5" aria-label="Deal stages">
        {stages.map((s) => (
          <li key={s.id} className="flex min-w-0 flex-col gap-1">
            <span className="flex items-center gap-1.5 text-xs font-semibold">
              <span aria-hidden="true" className={cn("inline-block size-3", stageMarker(s.reach))} />
              {s.label}
            </span>
            <span className="text-[11px] text-text-muted">{REACH_LABEL[s.reach]}</span>
            {s.steps.map((st) => (
              <StepEntry key={`${st.eventId}-${st.state}`} ticker={ticker} step={st} readOnly={readOnly} onChanged={onChanged} />
            ))}
          </li>
        ))}
      </ol>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <h4 className="text-xs font-semibold text-text-muted uppercase">Confirmed terms</h4>
          {terms.length ? (
            <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-sm">
              {terms.map((t) => (
                <div key={t.key} className="contents">
                  <dt className="text-text-muted">{t.label}</dt>
                  <dd className="num text-right">{t.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="mt-1 text-xs text-text-muted">No confirmed terms. Suggested terms are not used.</p>
          )}
        </div>
        <ProFormaBlock deal={deal} />
      </div>
      {deal.ignored.length ? (
        <p className="text-xs text-text-muted">
          Ignored events: {deal.ignored.map((i) => `#${i.eventId ?? "?"} (${i.reason})`).join("; ")}.
        </p>
      ) : null}
    </li>
  );
}

function StepEntry({ ticker, step, readOnly, onChanged }: { ticker: string; step: Deal["timeline"][number]; readOnly: boolean; onChanged: () => void }) {
  const decidable = step.eventId !== null;
  return (
    <div className={cn("flex flex-col gap-1 border-t border-border-soft pt-1 text-xs", step.status !== "confirmed" && "border-dashed")}>
      <span className="num">{step.date ? formatDate(step.date) : "undated"}</span>
      <span className="flex flex-wrap items-center gap-1">
        <RowStatusBadge status={step.status as RowStatus} />
        <TierBadge tier={step.tier} />
      </span>
      <SourceLink href={step.sourceUrl}>{step.source}</SourceLink>
      {step.status === "confirmed" && step.evidenceId === null ? <span className="text-text">Confirmed without linked evidence.</span> : null}
      {decidable ? (
        <details>
          <summary className="cursor-pointer text-accent-ink select-none hover:underline">Decide</summary>
          <DecisionControl status={step.status} readOnly={readOnly} onDecide={(d, r) => decideEvent(ticker, step.eventId as number, d, r)} onDone={onChanged} />
        </details>
      ) : null}
    </div>
  );
}

function ratio(v: number | null): string {
  return v === null ? "not computable" : `${v.toFixed(2)}x`;
}

function ProFormaBlock({ deal }: { deal: Deal }) {
  const p: ProForma | null = deal.proForma;
  return (
    <div>
      <h4 className="text-xs font-semibold text-text-muted uppercase">Dilution and leverage (confirmed terms only)</h4>
      {p === null ? (
        <p className="mt-1 text-xs text-text-muted">{deal.proFormaUnavailable ? `Not computed: ${deal.proFormaUnavailable}.` : "Not computed."}</p>
      ) : (
        <>
          <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-sm">
            <dt className="text-text-muted">Share count increase</dt>
            <dd className="num text-right">{p.shareCountIncrease === null ? "not computable" : formatPct(p.shareCountIncrease, 1)}</dd>
            <dt className="text-text-muted">New holders&apos; ownership</dt>
            <dd className="num text-right">{p.newHoldersOwnership === null ? "not computable" : formatPct(p.newHoldersOwnership, 1)}</dd>
            <dt className="text-text-muted">Gross leverage before / after</dt>
            <dd className="num text-right">{ratio(p.grossLeverageBefore)} / {ratio(p.grossLeverageAfter)}</dd>
            <dt className="text-text-muted">Net leverage before / after</dt>
            <dd className="num text-right">{ratio(p.netLeverageBefore)} / {ratio(p.netLeverageAfter)}</dd>
          </dl>
          {p.inputsMissing.length ? <p className="mt-1 text-xs text-text">Missing inputs: {p.inputsMissing.join("; ")}.</p> : null}
          <p className="mt-1 text-[11px] text-text-muted">{deal.proFormaBasis ?? "confirmed terms only"}; latest reported capital; leverage is debt over EBITDA.</p>
        </>
      )}
    </div>
  );
}
