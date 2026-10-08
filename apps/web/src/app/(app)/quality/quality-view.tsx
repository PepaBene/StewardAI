"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ISSUE_LABEL, weightBar, type Cockpit } from "@/lib/journal/journal";

const fetchCockpit = () => getBrowserApiClient().GET("/quality/cockpit");

export function QualityView() {
  const { state } = useApiResource(fetchCockpit);
  return (
    <ResourceGate state={state} label="the data-quality checks">
      {(cockpit) => <Body cockpit={cockpit} />}
    </ResourceGate>
  );
}

function Body({ cockpit }: { cockpit: Cockpit }) {
  const max = Math.max(0, ...cockpit.issues.map((i) => i.weight ?? 0));
  const portfolio = cockpit.issues.filter((i) => i.weight !== null && i.weight !== undefined);
  const other = cockpit.issues.filter((i) => i.weight === null || i.weight === undefined);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading
          level={2}
          kicker="Summary"
          title={cockpit.issues.length === 0 ? "No problems found by the checks that ran" : `${cockpit.issues.length} issue${cockpit.issues.length === 1 ? "" : "s"} to look at`}
        />
        <p className="mt-2 max-w-prose text-sm">
          {cockpit.issues.length === 0
            ? "Every check below ran and found nothing. The checks listed as not covered did not run."
            : `Together they touch ${formatPercent(cockpit.affected_weight, { decimals: 0 })} of the portfolio, counting each holding once.`}
        </p>
      </Blueprint>

      {portfolio.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Fix first" title="Issues on holdings, by share of the portfolio" />
          <ol className="mt-3 divide-y divide-border-soft border-y border-border-soft">
            {portfolio.map((issue, i) => (
              <li key={`${issue.kind}-${issue.subject}-${i}`} className="grid gap-x-4 gap-y-1 py-2.5 sm:grid-cols-[9rem_1fr_6rem]">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{issue.subject}</div>
                  <div className="text-xs text-text-muted">{ISSUE_LABEL[issue.kind]}</div>
                </div>
                <div className="min-w-0 text-sm">
                  <p>{issue.detail}</p>
                  <p className="text-xs text-text-muted">{issue.fix}</p>
                </div>
                <div className="flex items-center gap-2 sm:flex-col sm:items-end sm:gap-1">
                  <span className="num text-sm font-semibold">{formatPercent(issue.weight ?? 0, { decimals: 1 })}</span>
                  <span className="block h-1.5 w-full max-w-24 bg-border-soft" aria-hidden="true">
                    <span className="block h-full bg-accent" style={{ width: `${weightBar(issue.weight, max)}%` }} />
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </Blueprint>
      ) : null}

      {other.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Not tied to a holding" title="Sources and providers" />
          <ul className="mt-3 divide-y divide-border-soft border-y border-border-soft">
            {other.map((issue, i) => (
              <li key={`${issue.kind}-${issue.subject}-${i}`} className="py-2.5 text-sm">
                <span className="font-semibold">{issue.subject}</span> <span className="text-text-muted">· {ISSUE_LABEL[issue.kind]}</span>
                <p>{issue.detail}</p>
                <p className="text-xs text-text-muted">{issue.fix}</p>
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Checks that ran" title="And what each one covers" />
          <table className="mt-3 w-full text-sm">
            <caption className="sr-only">Data checks that ran, what each covers and how many issues it found</caption>
            <thead>
              <tr className="text-left text-xs text-text-muted">
                <th scope="col" className="py-1 font-medium">
                  Check
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Found
                </th>
              </tr>
            </thead>
            <tbody>
              {cockpit.checks.map((c) => (
                <tr key={c.kind} className="border-t border-border-soft align-top">
                  <th scope="row" className="py-1.5 text-left font-normal">
                    {c.title}
                    <div className="text-xs text-text-muted">Looked at: {c.denominator}</div>
                  </th>
                  <td className="num py-1.5 text-right">{c.issues}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Blueprint>
        <Blueprint className="border-dashed p-4">
          <PanelHeading level={3} kicker="Not covered yet" title="Checks that cannot run" />
          <ul className="mt-3 space-y-2 text-sm">
            {cockpit.not_covered.map((n) => (
              <li key={n.title}>
                <span className="font-semibold">{n.title}</span>
                <p className="text-text-muted">{n.reason}</p>
              </li>
            ))}
          </ul>
        </Blueprint>
      </div>
    </div>
  );
}
