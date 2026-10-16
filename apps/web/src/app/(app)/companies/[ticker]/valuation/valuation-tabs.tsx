"use client";

import { useCallback, useState } from "react";

import { BandGauge } from "@/components/insight/band-gauge";
import { Blueprint } from "@/components/ui/blueprint";
import { usePageSelection } from "@/lib/chat/page-selection-store";
import { cn } from "@/lib/utils";

import { MultipleView, type Headline } from "./multiple-view";
import { ValuationPictures } from "./valuation-pictures";
import { DcfView } from "./valuation-view";

/*
 * Two valuation methods side by side (insight I6.6): the DCF and the multiple path each keep their
 * own editor and saved versions; both stay mounted so switching tabs never loses an unsaved edit.
 * The comparison strip names each method's headline, scenario and version so the two numbers are
 * never read as one, and the gauge shows where the price sits against the active method's value.
 */

type Method = "dcf" | "multiple";
const METHODS: { key: Method; label: string }[] = [
  { key: "dcf", label: "Discounted cash flow" },
  { key: "multiple", label: "Multiple path" },
];

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

function HeadlineCard({ h, label, active, onPick }: { h: Headline | null; label: string; active: boolean; onPick: () => void }) {
  return (
    <button type="button" onClick={onPick} aria-pressed={active} className={cn("flex-1 border px-3 py-2 text-left", active ? "border-accent" : "border-border hover:border-text-faint")}>
      <span className="block text-xs text-text-muted">{label}</span>
      {h && h.value !== null ? (
        <>
          <span className="num block text-lg font-semibold">
            {money(h.value)} <span className="text-xs font-normal text-text-muted">{h.currency ?? ""}</span>
          </span>
          <span className="block text-xs text-text-muted">
            <span className="capitalize">{h.scenario}</span> scenario · {h.version !== null ? `saved v${h.version}` : "unsaved"}
          </span>
        </>
      ) : (
        <span className="block text-sm text-text-muted">{h ? "Not computable with these inputs" : "Computing…"}</span>
      )}
    </button>
  );
}

export function ValuationView({ ticker }: { ticker: string }) {
  const [method, setMethod] = useState<Method>("dcf");
  const [heads, setHeads] = useState<Record<Method, Headline | null>>({ dcf: null, multiple: null });
  const onDcf = useCallback((h: Headline | null) => setHeads((x) => ({ ...x, dcf: h })), []);
  const onMultiple = useCallback((h: Headline | null) => setHeads((x) => ({ ...x, multiple: h })), []);
  const active = heads[method];
  usePageSelection(`${method === "dcf" ? "DCF" : "multiple path"}${active ? `, scenario ${active.scenario}` : ""}`);
  const gap = priceGap(active);

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="space-y-4 px-[22px] py-5">
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
        <div className="flex flex-col gap-2 sm:flex-row">
          <HeadlineCard h={heads.dcf} label="DCF value per share" active={method === "dcf"} onPick={() => setMethod("dcf")} />
          <HeadlineCard h={heads.multiple} label="Multiple-path value per share" active={method === "multiple"} onPick={() => setMethod("multiple")} />
        </div>
        {active && active.price !== null ? (
          <BandGauge
            value={gap === null ? null : Math.max(-0.6, Math.min(0.6, gap))}
            min={-0.6}
            max={0.6}
            zones={[
              { label: "20%+ under", to: -0.2, tone: "gain" },
              { label: "About right", to: 0.2, tone: "neutral" },
              { label: "20%+ over", to: 0.6, tone: "loss" },
            ]}
            marker={gap === null ? "" : gapText(gap)}
            format={(v) => `${(v * 100).toFixed(0)}%`}
            label={`Last close ${money(active.price)} against the ${method === "dcf" ? "DCF" : "multiple-path"} ${active.scenario} value`}
          />
        ) : (
          <p className="text-xs text-text-muted">The price gauge appears once a value and a stored price are both available.</p>
        )}
      </Blueprint>
      <div role="tabpanel" id="panel-dcf" aria-labelledby="tab-dcf" hidden={method !== "dcf"}>
        <DcfView ticker={ticker} onHeadline={onDcf} />
      </div>
      <div role="tabpanel" id="panel-multiple" aria-labelledby="tab-multiple" hidden={method !== "multiple"}>
        <MultipleView ticker={ticker} onHeadline={onMultiple} />
      </div>
      <ValuationPictures ticker={ticker} />
    </div>
  );
}
