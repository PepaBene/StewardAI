"use client";

import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatRatio, toNumber, type Rubric, type RubricComponent, type RubricIndicator } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const BAND_TEXT: Record<RubricIndicator["band"], string> = { good: "Strong", ok: "Middling", weak: "Weak", missing: "Missing" };
const BAND_CLASS: Record<RubricIndicator["band"], string> = { good: "text-gain", ok: "text-text", weak: "text-loss", missing: "text-text-muted" };

function points(v: string | number | null | undefined): string | null {
  const n = toNumber(v);
  return n === null ? null : n.toFixed(0);
}

/** A 0 to 100 scale with the three band edges (20, 60, 100) marked; the marker is the score. */
function ScaleBar({ score, label }: { score: number | null; label: string }) {
  return (
    <div role="img" aria-label={score === null ? `${label}: no score` : `${label}: ${score.toFixed(0)} of 100`} className="relative h-2 w-full border border-border bg-surface-sunken">
      {[20, 60].map((edge) => (
        <span key={edge} className="absolute inset-y-0 w-px bg-border" style={{ left: `${edge}%` }} />
      ))}
      {score !== null ? <span className="absolute -inset-y-1 w-1 bg-accent" style={{ left: `calc(${Math.min(100, Math.max(0, score))}% - 2px)` }} /> : null}
    </div>
  );
}

function IndicatorRow({ ind }: { ind: RubricIndicator }) {
  const value = ind.value === null ? null : formatRatio(ind.value, ind.unit === "share" ? "fraction" : ind.unit);
  return (
    <tr className="border-t border-border-soft align-top">
      <th scope="row" className="px-3 py-1.5 text-left font-normal">
        {ind.label}
        <span className="block text-xs text-text-muted">{ind.source}</span>
      </th>
      <td className="num px-2 py-1.5 text-right">{value ?? <Absent reason={ind.reason} />}</td>
      <td className="px-2 py-1.5 text-right text-xs text-text-muted">
        {ind.direction === "higher" ? "strong at or above" : ind.direction === "abs_lower" ? "strong within ±" : "strong at or below"} {formatRatio(ind.good, ind.unit === "share" ? "fraction" : ind.unit)}
        <span className="block">middling to {formatRatio(ind.ok, ind.unit === "share" ? "fraction" : ind.unit)}</span>
      </td>
      <td className={cn("px-2 py-1.5 text-right text-sm font-medium", BAND_CLASS[ind.band])}>{BAND_TEXT[ind.band]}</td>
      <td className="num px-2 py-1.5 text-right">{points(ind.points) ?? ""}</td>
      <td className="num px-2 py-1.5 text-right text-xs text-text-muted">{(toNumber(ind.weight)! * 100).toFixed(0)}%</td>
    </tr>
  );
}

function ComponentPanel({ c }: { c: RubricComponent }) {
  const score = toNumber(c.score);
  const coverage = toNumber(c.coverage) ?? 0;
  return (
    <Blueprint aria-labelledby={`c-${c.id}`} className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker={`Weight ${(toNumber(c.weight)! * 100).toFixed(0)}% · evidence ${(coverage * 100).toFixed(0)}%`} title={<span id={`c-${c.id}`}>{c.label}</span>} />
      <p className="text-sm text-text-muted">{c.description}</p>
      <div className="grid items-center gap-x-6 gap-y-2 sm:grid-cols-[8rem_1fr]">
        <p className="num text-2xl">{c.abstained || score === null ? <Absent reason={c.reason} /> : score.toFixed(0)}</p>
        <ScaleBar score={c.abstained ? null : score} label={c.label} />
      </div>
      {c.abstained && c.reason ? <AlertMessage title="No score for this component">{c.reason}</AlertMessage> : null}
      {!c.abstained && c.raw_score !== null && c.raw_score !== c.score ? (
        <p className="text-xs text-text-muted">Before the missing-evidence penalty the component scored {points(c.raw_score)}.</p>
      ) : null}
      <div className="overflow-x-auto border border-border">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-sunken text-xs">
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Indicator</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Value</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Thresholds</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Band</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Points</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Weight</th>
            </tr>
          </thead>
          <tbody>
            {c.indicators.map((i) => (
              <IndicatorRow key={i.id} ind={i} />
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}

export function RubricBody({ rubric }: { rubric: Rubric }) {
  const overall = toNumber(rubric.overall);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint aria-labelledby="assessment-overall" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker={`StewardAI rubric ${rubric.version} · as of ${rubric.as_of}`} title={<span id="assessment-overall">Overall evidence score</span>} />
        <div className="grid items-center gap-x-6 gap-y-2 sm:grid-cols-[8rem_1fr]">
          <p className="num text-3xl">{rubric.abstained || overall === null ? <Absent reason={rubric.reason} /> : overall.toFixed(0)}</p>
          <ScaleBar score={rubric.abstained ? null : overall} label="Overall" />
        </div>
        {rubric.abstained && rubric.reason ? <AlertMessage title="No overall score">{rubric.reason}</AlertMessage> : null}
        <p className="text-sm text-text-muted">
          This is a summary of published indicators against stated thresholds, out of 100. It is not a rating, a forecast or advice, and it does not rank companies against each other. Evidence
          covered {((toNumber(rubric.overall_coverage) ?? 0) * 100).toFixed(0)}% of the weight.
        </p>
      </Blueprint>

      {rubric.components.map((c) => (
        <ComponentPanel key={c.id} c={c} />
      ))}

      <Blueprint aria-labelledby="assessment-rules" className="space-y-2 px-[22px] py-5">
        <PanelHeading kicker="How the score is built" title={<span id="assessment-rules">Rules and limits</span>} />
        <p className="text-sm">{rubric.penalty_rule}</p>
        <p className="text-sm">
          <span className="font-medium">Calibration: {rubric.calibration.status}.</span> {rubric.calibration.note}
        </p>
        {rubric.caveats.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-text-muted">
            {rubric.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : null}
        <p className="font-mono text-xs text-text-muted">Input hash {rubric.input_hash.slice(0, 16)}</p>
      </Blueprint>
    </div>
  );
}

export function AssessmentView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/assessment", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="the assessment">
      {(rubric: Rubric) => <RubricBody rubric={rubric} />}
    </ResourceGate>
  );
}
