"use client";

import { useCallback, useEffect, useState } from "react";

import { AssumptionPanel } from "@/components/company/assumption-panel";
import { useFairValueReport } from "@/components/company/company-context";
import { BandGauge, VALUATION_RANGE, VALUATION_ZONES } from "@/components/insight/band-gauge";
import { DotStrip, MIN_PLACED, priceVsFair } from "@/components/insight/dot-strip";
import { LimitedState } from "@/components/insight/limited-state";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { usePageSelection } from "@/lib/chat/page-selection-store";
import { toNumber } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import { MultipleView, type Headline } from "./multiple-view";
import { ValuationPictures } from "./valuation-pictures";
import { DcfView } from "./valuation-view";

/*
 * The Valuation section, answer-first (redesign plan R4.4). The page opens with one verdict: a
 * band gauge of the price against the chosen fair value, with the basis named. Below it the price
 * is set against every fair value that could be computed, so two valuations never compete
 * silently. The two scenario editors (discounted cash flow and multiple path) live in the
 * "Edit assumptions" side panel; both stay mounted so closing the panel never loses an edit.
 */

type Method = "dcf" | "multiple";
const METHODS: { key: Method; label: string }[] = [
  { key: "dcf", label: "Discounted cash flow" },
  { key: "multiple", label: "Multiple path" },
];
const LABEL: Record<Method, string> = { dcf: "Discounted cash flow", multiple: "Multiple path" };

const money = (v: number) => v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Price against value, as a fraction: negative when the price sits under the value. */
export function priceGap(h: Headline | null): number | null {
  if (!h || h.value === null || h.price === null || h.value <= 0) return null;
  return h.price / h.value - 1;
}

function gapText(g: number): string {
  const p = Math.abs(g * 100).toFixed(0);
  if (Math.abs(g) < 0.005) return "Price at the value";
  return g < 0 ? `Price ${p}% under the value` : `Price ${p}% over the value`;
}

function basisText(method: Method, h: Headline): string {
  return `${LABEL[method]}, ${h.scenario} scenario, ${h.version !== null ? `saved v${h.version}` : "unsaved"}`;
}

type StoredFairValues = components["schemas"]["CompanyFairValues"];

/** Stored values from outside the two editors: the analyst mean, your thesis target, the peer multiple. */
const STORED_SOURCES = ["analyst_mean", "thesis_target", "peer_multiple"];

type CompareRow = { key: string; label: string; value: number; currency: string | null };

/** The live editor results, then the stored outside values, each once. */
export function comparisonRows(heads: Record<Method, Headline | null>, stored: StoredFairValues | null): CompareRow[] {
  const live = METHODS.flatMap((m) => {
    const h = heads[m.key];
    return h && h.value !== null ? [{ key: m.key, label: LABEL[m.key], value: h.value, currency: h.currency }] : [];
  });
  const outside = (stored?.values ?? []).flatMap((v) => {
    const value = v.value === null ? null : Number(v.value);
    return STORED_SOURCES.includes(v.source) && value !== null && Number.isFinite(value) ? [{ key: v.source, label: v.label, value, currency: v.currency }] : [];
  });
  return [...live, ...outside];
}

/** Price against each fair value: a labelled strip from three values, plain rows below that. */
function ComparisonStrip({ heads, stored }: { heads: Record<Method, Headline | null>; stored: StoredFairValues | null }) {
  const rows = comparisonRows(heads, stored);
  const price = METHODS.map((m) => heads[m.key]?.price).find((p) => p !== null && p !== undefined) ?? toNumber(stored?.price);
  if (rows.length === 0 || price === null || price === undefined) return null;
  const low = stored?.values.find((v) => v.source === "analyst_low")?.value;
  const high = stored?.values.find((v) => v.source === "analyst_high")?.value;
  const range = low !== null && low !== undefined && high !== null && high !== undefined ? `Analyst targets run from ${money(Number(low))} to ${money(Number(high))}.` : null;
  const dots = rows.map((r) => ({ key: r.key, label: r.label, value: priceVsFair(price, r.value) }));
  return (
    <div className="space-y-2">
      {rows.length >= MIN_PLACED ? (
        <DotStrip dots={dots} convention="price-vs-fair" format={(v) => `${(v * 100).toFixed(0)}%`} label="Price against each fair value" />
      ) : (
        <ul className="space-y-1 text-sm" aria-label="Price against each fair value">
          {rows.map((r) => {
            const g = price / r.value - 1;
            return (
              <li key={r.key} className="flex flex-wrap justify-between gap-x-3">
                <span>{r.label}</span>
                <span className="num text-text-muted">
                  {money(r.value)} {r.currency ?? ""}, {gapText(g).toLowerCase()}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {range ? <p className="text-xs text-text-muted">{range}</p> : null}
    </div>
  );
}

export function ValuationView({ ticker }: { ticker: string }) {
  const [method, setMethod] = useState<Method>("dcf");
  const [heads, setHeads] = useState<Record<Method, Headline | null>>({ dcf: null, multiple: null });
  const [panelOpen, setPanelOpen] = useState(false);
  const onDcf = useCallback((h: Headline | null) => setHeads((x) => ({ ...x, dcf: h })), []);
  const onMultiple = useCallback((h: Headline | null) => setHeads((x) => ({ ...x, multiple: h })), []);
  const { setReport } = useFairValueReport();
  const storedFetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/fair-values", { params: { path: { ticker } } }), [ticker]);
  const { state: storedState } = useApiResource(storedFetcher);
  const stored = storedState.kind === "ok" ? storedState.data : null;

  // Fall back to the other method when the chosen one cannot be computed.
  const usable = (h: Headline | null) => h !== null && h.value !== null;
  const basis: Method = usable(heads[method]) || !usable(heads[method === "dcf" ? "multiple" : "dcf"]) ? method : method === "dcf" ? "multiple" : "dcf";
  const active = heads[basis];
  const gap = priceGap(active);
  usePageSelection(`${LABEL[basis]}${active ? `, scenario ${active.scenario}` : ""}`);

  useEffect(() => {
    if (active && active.value !== null) setReport({ value: active.value, currency: active.currency, basis: basisText(basis, active), price: active.price });
    else setReport(null);
    return () => setReport(null);
  }, [active, basis, setReport]);

  const resultCard =
    active && active.value !== null ? (
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="kpi-figure num">
          {money(active.value)} <span className="text-sm font-normal text-text-muted">{active.currency ?? ""} per share</span>
        </p>
        <p className="text-sm text-text-muted">{gap !== null ? gapText(gap) : basisText(basis, active)}</p>
      </div>
    ) : (
      <p className="text-sm text-text-muted">Computing the fair value…</p>
    );

  return (
    <div className="flex flex-col gap-5">
      <Blueprint aria-labelledby="val-answer" className="space-y-4 px-[22px] py-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 id="val-answer" className="panel-title">
              {gap === null ? "Where the price sits against fair value" : gapText(gap)}
            </h3>
            <p className="text-sm text-text-muted">{active && active.value !== null ? `Basis: ${basisText(basis, active)}.` : "Basis: not chosen yet."}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => setPanelOpen(true)}>
            Edit assumptions
          </Button>
        </div>
        <div role="group" aria-label="Fair value basis" className="inline-flex rounded-control bg-surface-muted p-0.5">
          {METHODS.map((m) => (
            <button
              key={m.key}
              type="button"
              aria-pressed={basis === m.key}
              onClick={() => setMethod(m.key)}
              className={cn("rounded-control px-3 py-1 text-sm", basis === m.key ? "bg-surface-raised font-semibold text-text" : "text-text-muted hover:text-text")}
            >
              {m.label}
            </button>
          ))}
        </div>
        {active && active.price !== null && gap !== null ? (
          <BandGauge
            value={Math.max(VALUATION_RANGE.min, Math.min(VALUATION_RANGE.max, gap))}
            min={VALUATION_RANGE.min}
            max={VALUATION_RANGE.max}
            zones={VALUATION_ZONES}
            marker={`Price ${money(active.price)} · ${gapText(gap).toLowerCase()}`}
            format={(v) => `${(v * 100).toFixed(0)}%`}
            label={`Last close ${money(active.price)} against the ${LABEL[basis].toLowerCase()} ${active.scenario} value`}
          />
        ) : (
          <LimitedState>The gauge appears once a fair value and a stored price are both available.</LimitedState>
        )}
        <ComparisonStrip heads={heads} stored={stored} />
      </Blueprint>

      <AssumptionPanel open={panelOpen} onClose={() => setPanelOpen(false)} result={resultCard}>
        <div role="tablist" aria-label="Valuation method" className="flex border-b border-border">
          {METHODS.map((m) => (
            <button
              key={m.key}
              type="button"
              role="tab"
              id={`tab-${m.key}`}
              aria-controls={`panel-${m.key}`}
              aria-selected={method === m.key}
              onClick={() => setMethod(m.key)}
              className={cn("-mb-px border-b-2 px-3 py-1.5 text-sm", method === m.key ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text")}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="panel-dcf" aria-labelledby="tab-dcf" hidden={method !== "dcf"}>
          <DcfView ticker={ticker} onHeadline={onDcf} />
        </div>
        <div role="tabpanel" id="panel-multiple" aria-labelledby="tab-multiple" hidden={method !== "multiple"}>
          <MultipleView ticker={ticker} onHeadline={onMultiple} />
        </div>
      </AssumptionPanel>

      <ValuationPictures ticker={ticker} />
    </div>
  );
}
