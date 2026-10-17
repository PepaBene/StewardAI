"use client";

import Link from "next/link";
import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import type { PortfolioScorecard } from "@/lib/insight/checks";
import { AXIS_LABEL, MARK_AXES, num, petalsFromMap, type MarkAxis } from "@/lib/insight/insight";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ScoreMark, ScoreMarkKey } from "./score-mark";

const fetchScorecard = () => getBrowserApiClient().GET("/portfolio/scorecard");

/*
 * Portfolio scorecard (insight I4.3): the large five-axis mark with clickable axes; the selected
 * axis shows its value-weighted score, a one-line explanation, its coverage and the holdings
 * lifting and holding back the score.
 */
export function ScorecardPanel({ className }: { className?: string }) {
  const { state } = useApiResource(fetchScorecard);
  return (
    <Blueprint className={cn("px-[22px] py-4", className)}>
      <PanelHeading kicker="Value-weighted across holdings" title="Portfolio scorecard" />
      <div className="mt-3">
        <ResourceGate state={state} label="scorecard">
          {(s: PortfolioScorecard) => <Scorecard s={s} />}
        </ResourceGate>
      </div>
    </Blueprint>
  );
}

function Lines({ title, lines, tone }: { title: string; lines: PortfolioScorecard["axes"][number]["lifting"]; tone: "gain" | "loss" }) {
  return (
    <div>
      <h3 className="text-sm font-semibold">{title}</h3>
      {lines.length === 0 ? (
        <p className="mt-1 text-xs text-text-faint">None</p>
      ) : (
        <ul className="mt-1 space-y-1.5">
          {lines.map((l) => {
            const score = num(l.score);
            return (
              <li key={l.ticker} className="text-xs">
                <div className="flex justify-between gap-2">
                  <Link href={`/companies/${encodeURIComponent(l.ticker)}/checks`} className="truncate hover:underline">
                    {l.name ?? l.ticker}
                  </Link>
                  <span className="num shrink-0 text-text-muted">
                    {l.passes} of {l.applicable} · {formatPercent(num(l.weight), { decimals: 1 })} of invested
                  </span>
                </div>
                <div className="mt-0.5 h-1 bg-score-mark-track">
                  <div className={cn("h-full", tone === "gain" ? "bg-gain" : "bg-loss")} style={{ width: `${score === null ? 0 : (score / 6) * 100}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Scorecard({ s }: { s: PortfolioScorecard }) {
  const [axis, setAxis] = useState<MarkAxis>("value");
  if (s.status === "unavailable" && s.axes.every((a) => num(a.score) === null)) {
    return (
      <p className="text-sm text-text-muted">
        {s.note}
        {s.unchecked.length ? ` Not checked yet: ${s.unchecked.join(", ")}. Opening a company computes its checks.` : ""}
      </p>
    );
  }
  const scores = Object.fromEntries(s.axes.map((a) => [a.axis, a.score]));
  const sel = s.axes.find((a) => a.axis === axis);
  const score = num(sel?.score);
  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,260px)_minmax(0,1fr)]">
      <div className="mx-auto w-full max-w-[260px]">
        <ScoreMark petals={petalsFromMap(scores)} size="card" selected={axis} onSelect={setAxis} label="Portfolio" />
        <ScoreMarkKey className="mt-1 justify-center" />
      </div>
      <div className="min-w-0">
        <div role="tablist" aria-label="Axis" className="flex flex-wrap gap-1">
          {MARK_AXES.map((a) => (
            <button
              key={a}
              type="button"
              role="tab"
              aria-selected={axis === a}
              onClick={() => setAxis(a)}
              className={cn("border px-2 py-0.5 text-xs", axis === a ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted")}
            >
              {AXIS_LABEL[a]}
            </button>
          ))}
        </div>
        {sel ? (
          <>
            <p className="mt-3 flex items-baseline gap-2">
              <span className="num font-heading text-3xl font-semibold">{score === null ? "unknown" : `${score.toFixed(1)} / 6`}</span>
              <span className="text-xs text-text-muted">{sel.coverage_text}</span>
            </p>
            <p className="mt-1 text-sm text-text-muted">{sel.explanation}</p>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Lines title="Lifting" lines={sel.lifting} tone="gain" />
              <Lines title="Holding back" lines={sel.holding_back} tone="loss" />
            </div>
          </>
        ) : null}
        {s.unchecked.length ? <p className="mt-3 text-xs text-text-faint">Not checked yet: {s.unchecked.join(", ")}.</p> : null}
        <p className="mt-1 text-xs text-text-faint">{s.note}</p>
      </div>
    </div>
  );
}
