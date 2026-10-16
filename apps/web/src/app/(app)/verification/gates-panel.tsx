"use client";

import { Panel, ResourceNotice } from "@/components/oversight/panel";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { getBrowserApiClient } from "@/lib/api/client";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";
import { cn } from "@/lib/utils";
import type { Conclusion, DomainGate, ZeroCostGate } from "@/lib/verification/verification";

export const fetchZero = () => getBrowserApiClient().GET("/verification/zero-cost-gate");
export const fetchDomains = () => getBrowserApiClient().GET("/verification/domain-gates");

/** A gate verdict as a word plus a mark; never a gain/loss colour. */
export function Verdict({ ok, yes = "Open", no = "Blocked" }: { ok: boolean; yes?: string; no?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-control border px-1.5 py-0.5 text-xs font-semibold",
        ok ? "border-accent text-accent-ink" : "border-warning-signal text-text",
      )}
    >
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", ok ? "bg-accent" : "bg-warning-signal")} />
      {ok ? yes : no}
    </span>
  );
}

/** The zero-cost audit and every reconciliation gate as check rows, blocked ones first. */
export function GatesPanel({
  zero,
  domains,
  z,
  d,
}: {
  /** The two fetches, owned by the page so the summary tiles and this panel share one request each. */
  zero: { state: ResourceState<unknown> };
  domains: { state: ResourceState<unknown> };
  z: ZeroCostGate | null;
  d: { domains: DomainGate[]; conclusions: Conclusion[] } | null;
}) {

  const gateRows: StatusRow[] = [];
  if (z) {
    gateRows.push({
      id: "zero-cost",
      state: z.passed ? "pass" : "fail",
      lead: "Zero-cost audit",
      sentence: z.passed
        ? `Nothing paid is reachable. ${z.checked ?? "Unknown"} endpoints checked, ${z.freeAvailable ?? "unknown"} free and available.`
        : `A paid endpoint is reachable (${z.violations ?? "unknown"} violations). Turn it off in Data sources.`,
      details: (
        <>
          <p>
            {z.disabledOk ?? "Unknown"} disabled · {z.gatedUnverified ?? "Unknown"} gated and unverified · {z.warnings ?? "Unknown"} warnings
          </p>
          {z.findings.map((f, i) => (
            <p key={i}>
              {f.provider} {f.endpoint} · {titleCase(f.severity)} · {f.code}: {f.message}
            </p>
          ))}
        </>
      ),
      href: "/data-sources#access",
      hrefLabel: "Open Data sources",
    });
  }
  for (const g of d?.domains ?? []) {
    gateRows.push({
      id: `domain-${g.domain}`,
      state: g.samples === null && g.failures === null ? "unknown" : g.allowed ? "pass" : "fail",
      lead: `${titleCase(g.domain)} reconciliation`,
      sentence:
        g.samples === null
          ? "Not measured yet, so conclusions that need it stay closed."
          : g.allowed
            ? `Open: ${g.samples} samples agree with the source${g.failures ? `, ${g.failures} failed` : ""}.`
            : `Blocked: ${g.failures ?? "unknown"} of ${g.samples} samples disagree with the source.`,
      details: g.reasons.length > 0 ? <p>{g.reasons.join("; ")}</p> : undefined,
    });
  }

  const conclusionRows: StatusRow[] = (d?.conclusions ?? []).map((c) => ({
    id: `conclusion-${c.name}`,
    state: c.allowed ? "pass" : "fail",
    lead: titleCase(c.name),
    sentence: c.allowed
      ? `Open: every domain it rests on is open (${c.domains.map(titleCase).join(", ")}).`
      : `Blocked until ${c.domains.map(titleCase).join(", ")} ${c.domains.length === 1 ? "is" : "are"} open.`,
  }));

  return (
    <Panel
      id="gates"
      title="Gates"
      rounded
      description="The zero-cost audit checks that nothing paid is reachable. Each reconciliation domain is open only when its measured samples agree; a conclusion is open only when every domain it rests on is."
    >
      <ResourceNotice state={zero.state} what="zero-cost gate" />
      <ResourceNotice state={domains.state} what="domain gates" />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        {gateRows.length > 0 ? (
          <div>
            <h3 className="text-sm font-semibold">Audits and reconciliation by domain</h3>
            <StatusList caption="Zero-cost audit and reconciliation gates" passWord="are open" rows={gateRows} />
          </div>
        ) : null}
        {conclusionRows.length > 0 ? (
          <div>
            <h3 className="text-sm font-semibold">Conclusions and the domains they need</h3>
            <StatusList caption="Conclusions and the gates they need" passWord="are open" rows={conclusionRows} />
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
