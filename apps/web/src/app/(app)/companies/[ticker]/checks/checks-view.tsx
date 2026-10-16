"use client";

import Link from "next/link";
import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { CheckList } from "@/components/insight/check-list";
import { ScoreMark, ScoreMarkKey } from "@/components/insight/score-mark";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatDateTime } from "@/lib/format";
import { useCompanyChecks, type CompanyChecks } from "@/lib/insight/checks";
import { AXIS_LABEL, axisCoverage, markPetals, MARK_AXES, scoreText, type MarkAxis } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

export function ChecksView({ ticker }: { ticker: string }) {
  const { state } = useCompanyChecks(ticker);
  return (
    <ResourceGate state={state} label="checks">
      {(c: CompanyChecks) => <Body c={c} />}
    </ResourceGate>
  );
}

function Body({ c }: { c: CompanyChecks }) {
  const [axis, setAxis] = useState<MarkAxis>("value");
  const petals = markPetals(c.axes);
  const selected = c.axes.find((a) => a.axis === axis);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-5">
        <div className="grid items-center gap-6 md:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
          <div className="mx-auto w-full max-w-[300px]">
            <ScoreMark petals={petals} size="card" selected={axis} onSelect={setAxis} label={c.name ?? c.ticker} />
            <ScoreMarkKey className="mt-2 justify-center" />
          </div>
          <div className="min-w-0">
            <div className="kicker">Five axes, six checks each</div>
            <p className="mt-1 text-lg leading-snug">{c.verdict}</p>
            <div role="tablist" aria-label="Axis" className="mt-4 grid grid-cols-5 gap-1">
              {MARK_AXES.map((a) => {
                const s = c.axes.find((x) => x.axis === a);
                return (
                  <button
                    key={a}
                    type="button"
                    role="tab"
                    aria-selected={axis === a}
                    onClick={() => setAxis(a)}
                    className={cn("border px-2 py-1.5 text-left", axis === a ? "border-accent bg-accent-soft" : "border-border hover:border-text-faint")}
                  >
                    <span className="block text-xs text-text-muted">{AXIS_LABEL[a]}</span>
                    <span className="num block text-base font-semibold">{s ? scoreText(s) : "—"}</span>
                  </button>
                );
              })}
            </div>
            {selected ? <p className="mt-2 text-xs text-text-muted">{axisCoverage(selected)}. Not-applicable and unknown checks never count as a fail.</p> : null}
            <p className="mt-3 text-xs text-text-faint">
              Computed {formatDateTime(c.computed_at)} · method {c.method_version}, sentences {c.sentences_version}
              {c.rules_version ? (
                <>
                  {" "}
                  · rules{" "}
                  <Link href="/config/checks" className="underline underline-offset-2">
                    version {c.rules_version}
                  </Link>
                </>
              ) : (
                " · default rules"
              )}
            </p>
          </div>
        </div>
      </Blueprint>
      {c.reason ? <VerdictLine verdict="unknown">{c.reason}</VerdictLine> : null}
      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker={selected ? scoreText(selected) : undefined} title={AXIS_LABEL[axis]} />
          <CheckList className="mt-2" checks={c.checks.filter((x) => x.axis === axis)} ticker={c.ticker} />
        </Blueprint>
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker="Warning signs, failed first" title="Risk checks" />
          <CheckList className="mt-2" checks={c.risks} ticker={c.ticker} failedFirst />
        </Blueprint>
      </div>
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Every axis" title="All checks" level={3} />
        <div className="mt-3 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {MARK_AXES.map((a) => (
            <CheckList key={a} title={AXIS_LABEL[a]} checks={c.checks.filter((x) => x.axis === a)} ticker={c.ticker} failedFirst />
          ))}
        </div>
      </Blueprint>
    </div>
  );
}
