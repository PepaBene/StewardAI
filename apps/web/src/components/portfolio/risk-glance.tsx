"use client";

import Link from "next/link";

import { BandGauge, type GaugeZone } from "@/components/insight/band-gauge";
import { LimitedState } from "@/components/insight/limited-state";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

/*
 * Risk at a glance (plan 5.1 row 2): the 0-100 risk score on a band gauge with the market as a
 * second marker, then the score's tiles with their backend verdicts. An unscored portfolio shows
 * one LimitedState with the reason, not a gauge without a marker.
 */

// The bands of `steward.decision.risk_score.SCORE_BANDS`.
export const RISK_ZONES: readonly GaugeZone[] = [
  { label: "Lower than the market", to: 35, tone: "gain" },
  { label: "About the market", to: 55, tone: "neutral" },
  { label: "Riskier", to: 75, tone: "neutral" },
  { label: "Much riskier", to: 100, tone: "loss" },
];

const fetchScore = () => getBrowserApiClient().GET("/portfolio/risk/score");

/** "Not scored: <reason>." once, whether or not the backend's reason already carries the prefix. */
export function notScored(reason: string | null | undefined): string {
  const body = (reason ?? "an input is missing, so no score is given").replace(/^not scored:\s*/i, "").replace(/\.+$/, "");
  return `Not scored: ${body}.`;
}

function tileValue(key: string, unit: string | undefined, v: number): string {
  return unit === "ratio" || key === "beta" ? v.toFixed(2) : formatPercent(v, { decimals: 1 });
}

export function RiskGlance() {
  const { state } = useApiResource(fetchScore);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const score = data?.score ?? null;
  const markers =
    score && score.benchmark_score !== null && score.benchmark_score !== undefined
      ? [{ value: score.benchmark_score, label: data?.benchmark_symbol ? `Market (${data.benchmark_symbol})` : "Market" }]
      : [];

  return (
    <Blueprint className="flex min-w-0 flex-col gap-3 px-6 py-5.5" aria-label="Risk at a glance">
      <PanelHeading
        kicker="Risk"
        title="Risk at a glance"
        action={
          <Link href="/risk" className="text-xs font-medium text-accent-ink hover:underline">
            Open risk
          </Link>
        }
      />
      {!data ? (
        <p className="text-sm text-text-muted" role="status">
          {state.kind === "error" ? "The risk score could not be loaded." : "Loading the risk score…"}
        </p>
      ) : score && score.status !== "unknown" && score.score !== null && score.score !== undefined ? (
        <>
          <p className="text-sm text-text-muted">
            <span className="num font-semibold text-text">{Math.round(score.score)}</span> out of 100{score.band ? `: ${score.band}` : ""}.
          </p>
          <BandGauge
            value={score.score}
            min={0}
            max={100}
            zones={RISK_ZONES}
            marker="Your portfolio"
            markers={markers}
            format={(v) => Math.round(v).toString()}
            label="Risk score from 0 (no risk) to 100 (very high)"
          />
        </>
      ) : (
        <LimitedState fixHref="/risk" fixLabel="See what is missing">
          {notScored(score?.reason)}
        </LimitedState>
      )}
      {data && data.tiles.length > 0 ? (
        <dl className="grid grid-cols-1 gap-3 border-t border-border-soft pt-3 sm:grid-cols-3">
          {data.tiles.slice(0, 3).map((t) => (
            <div key={t.key} className="min-w-0">
              <dt className="truncate text-xs text-text-muted">{t.label}</dt>
              <dd className="num font-heading text-xl font-semibold">
                {t.value === null || t.value === undefined ? "—" : tileValue(t.key, t.unit, t.value)}
              </dd>
              {t.verdict ? (
                <VerdictLine verdict={t.verdict.tone} className="mt-1 text-xs">
                  {t.verdict.phrase}
                </VerdictLine>
              ) : null}
            </div>
          ))}
        </dl>
      ) : null}
    </Blueprint>
  );
}
