"use client";

import { useMemo } from "react";

import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";
import { parseDomainGates, parseZeroCost } from "@/lib/verification/verification";
import { cn } from "@/lib/utils";

const fetchZero = () => getBrowserApiClient().GET("/verification/zero-cost-gate");
const fetchDomains = () => getBrowserApiClient().GET("/verification/domain-gates");

/** A gate verdict as a word plus a bordered mark; never a gain/loss colour. */
export function Verdict({ ok, yes = "Open", no = "Blocked" }: { ok: boolean; yes?: string; no?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 border px-1.5 py-0.5 text-[11px] font-semibold uppercase", ok ? "border-accent text-accent-ink" : "border-warning-signal text-text")}>
      <span aria-hidden="true" className={cn("size-1.5", ok ? "bg-accent" : "bg-warning-signal")} />
      {ok ? yes : no}
    </span>
  );
}

const UNKNOWN = <span className="text-text-muted">Not measured</span>;

export function GatesPanel() {
  const zero = useApiResource(fetchZero);
  const domains = useApiResource(fetchDomains);
  const z = useMemo(() => (dataOf(zero.state) ? parseZeroCost(dataOf(zero.state)) : null), [zero.state]);
  const d = useMemo(() => (dataOf(domains.state) ? parseDomainGates(dataOf(domains.state)) : null), [domains.state]);

  return (
    <Panel
      id="gates"
      title="Gates"
      description="The zero-cost audit checks that nothing paid is reachable. Each reconciliation domain is open only when its measured samples agree; a conclusion is open only when every domain it rests on is."
    >
      <ResourceNotice state={zero.state} what="zero-cost gate" />
      {z ? (
        <div className="space-y-2 border border-border-soft px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-sm font-semibold">Zero-cost gate</h3>
            <Verdict ok={z.passed} yes="Nothing paid is reachable" no="Paid endpoint reachable" />
          </div>
          <p className="num text-xs text-text-muted">
            {z.checked ?? "Unknown"} endpoints checked · {z.freeAvailable ?? "Unknown"} free and available · {z.disabledOk ?? "Unknown"} disabled · {z.gatedUnverified ?? "Unknown"} gated and unverified · {z.violations ?? "Unknown"} violations · {z.warnings ?? "Unknown"} warnings
          </p>
          {z.findings.length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer text-accent-ink hover:underline">{z.findings.length} findings</summary>
              <ul className="mt-2 space-y-1 text-xs">
                {z.findings.map((f, i) => (
                  <li key={i}>
                    <span className="font-mono">
                      {f.provider} {f.endpoint}
                    </span>{" "}
                    <span className="text-text-muted">
                      {titleCase(f.severity)} · {f.code}: {f.message}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      <ResourceNotice state={domains.state} what="domain gates" />
      {d ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="mb-1 text-left text-sm font-semibold">Reconciliation by domain</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Domain</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Gate</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Samples</th>
                  <th scope="col" className="py-1.5 text-right font-medium">Failures</th>
                </tr>
              </thead>
              <tbody>
                {d.domains.map((g) => (
                  <tr key={g.domain} className="border-b border-border-soft align-top">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                      {titleCase(g.domain)}
                      {g.reasons.length > 0 ? <span className="block text-[11px] text-text-muted">{g.reasons.join("; ")}</span> : null}
                    </th>
                    <td className="py-1.5 pr-3"><Verdict ok={g.allowed} /></td>
                    <td className="num py-1.5 pr-3 text-right">{g.samples ?? UNKNOWN}{g.subjects !== null ? <span className="block text-[11px] text-text-muted">{g.subjects} subjects</span> : null}</td>
                    <td className="num py-1.5 text-right">{g.failures ?? UNKNOWN}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="mb-1 text-left text-sm font-semibold">Conclusions and the domains they need</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Conclusion</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Needs</th>
                  <th scope="col" className="py-1.5 font-medium">Gate</th>
                </tr>
              </thead>
              <tbody>
                {d.conclusions.map((c) => (
                  <tr key={c.name} className="border-b border-border-soft">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">{titleCase(c.name)}</th>
                    <td className="py-1.5 pr-3 text-xs text-text-muted">{c.domains.map(titleCase).join(", ")}</td>
                    <td className="py-1.5"><Verdict ok={c.allowed} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
