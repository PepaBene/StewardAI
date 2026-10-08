"use client";

import Link from "next/link";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { StatusDot } from "@/components/ui/status-dot";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { auditState, STATUS_LABEL, type FeatureReadiness, type Readiness, type Status } from "@/lib/release/release";
import { cn } from "@/lib/utils";

const fetchReadiness = () => getBrowserApiClient().GET("/release/readiness");

const TONE: Record<Status, "neutral" | "info" | "warning"> = {
  ready: "info",
  partial: "warning",
  untested: "neutral",
  unavailable: "neutral",
};

export function ReadinessView() {
  const { state } = useApiResource(fetchReadiness);
  return (
    <ResourceGate state={state} label="the release checks">
      {(readiness) => <Body readiness={readiness} />}
    </ResourceGate>
  );
}

function Body({ readiness }: { readiness: Readiness }) {
  const total = readiness.features.length;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading
          level={2}
          kicker="Summary"
          title={`${readiness.activatable} of ${total} features may be switched on`}
        />
        <p className="mt-2 max-w-prose text-sm">
          A feature passes three checkpoints: its inputs are reachable on the free tier and hold data, a sample of its facts matches the source, and the zero-cost audit passes.{" "}
          {readiness.ready} of {total} have their inputs in place; the rest are labelled below with the reason.
        </p>
        <p className={cn("mt-2 max-w-prose border-l-2 px-3 py-1.5 text-sm", readiness.zero_cost_passed ? "border-info-signal" : "border-warning-signal bg-warning-signal/5")}>
          {readiness.zero_cost_passed
            ? "Zero-cost audit passed: no endpoint can spend money."
            : `Zero-cost audit failed with ${readiness.zero_cost_violations} violation${readiness.zero_cost_violations === 1 ? "" : "s"}. No feature may be switched on until it passes. See Data sources.`}
        </p>
      </Blueprint>

      <ul className="flex flex-col gap-3">
        {readiness.features.map((f) => (
          <li key={f.id}>
            <FeatureGate feature={f} zeroCostPassed={readiness.zero_cost_passed} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function FeatureGate({ feature, zeroCostPassed }: { feature: FeatureReadiness; zeroCostPassed: boolean }) {
  const status = STATUS_LABEL[feature.status];
  const audit = auditState(feature);
  const open = feature.status === "unavailable" || feature.status === "untested";
  return (
    <Blueprint className={cn("p-4", open && "border-dashed")}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <h3 className="font-heading text-base font-semibold">{feature.title}</h3>
          <p className="text-xs text-text-muted">Plan {feature.plan}</p>
        </div>
        <p className="flex items-center gap-2 text-sm font-semibold">
          <StatusDot tone={TONE[feature.status]} />
          {feature.can_activate ? "May be switched on" : `Stays off: ${status.label.toLowerCase()}`}
        </p>
      </div>

      <dl className="mt-3 grid gap-px border border-border-soft bg-border-soft sm:grid-cols-3">
        <Checkpoint
          term="Free access"
          passed={feature.status === "ready"}
          value={status.label}
          note={feature.reason}
        />
        <Checkpoint
          term="Source audit"
          passed={audit.passed}
          value={audit.passed ? "Passed" : feature.audit === "mismatch" ? "Failed" : "Open"}
          note={audit.label}
          action={<Link href="/release/audit" className="underline underline-offset-2">Compare facts</Link>}
        />
        <Checkpoint
          term="Zero cost"
          passed={zeroCostPassed}
          value={zeroCostPassed ? "Passed" : "Failed"}
          note={zeroCostPassed ? "No paid endpoint is reachable." : "The zero-cost audit has violations."}
        />
      </dl>

      {feature.limit ? <p className="mt-3 max-w-prose text-sm text-text-muted">Known limit: {feature.limit}</p> : null}

      {feature.inputs.length > 0 ? (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-text-muted underline-offset-2 hover:text-text hover:underline focus-visible:outline-2 focus-visible:outline-focus">
            Inputs ({feature.inputs.length})
          </summary>
          <table className="mt-2 w-full">
            <caption className="sr-only">Inputs of {feature.title} and their state</caption>
            <thead>
              <tr className="text-left text-xs text-text-muted">
                <th scope="col" className="py-1 font-medium">
                  Input
                </th>
                <th scope="col" className="py-1 font-medium">
                  Needed for
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  State
                </th>
              </tr>
            </thead>
            <tbody>
              {feature.inputs.map((i) => (
                <tr key={i.label} className="border-t border-border-soft">
                  <th scope="row" className="py-1.5 text-left font-normal">
                    {i.label}
                    <span className="ml-1 text-xs text-text-muted">{i.kind === "store" ? "stored data" : "free-tier endpoint"}</span>
                  </th>
                  <td className="py-1.5">{i.required ? "Core" : "Extra"}</td>
                  <td className="py-1.5 text-right">
                    {i.ok ? "OK" : "Not OK"}: {i.detail}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </Blueprint>
  );
}

/** One checkpoint on the gate. The word carries the state; the marker only repeats it. */
function Checkpoint({
  term,
  passed,
  value,
  note,
  action,
}: {
  term: string;
  passed: boolean;
  value: string;
  note: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="bg-surface-raised p-3">
      <dt className="flex items-center gap-2 text-xs text-text-muted">
        <span aria-hidden="true" className={cn("size-2.5 border", passed ? "border-info-signal bg-info-signal" : "border-text-muted border-dashed")} />
        {term}
      </dt>
      <dd className="mt-1">
        <p className="text-sm font-semibold">{value}</p>
        <p className="text-xs text-text-muted">{note}</p>
        {action && !passed ? <p className="mt-1 text-xs">{action}</p> : null}
      </dd>
    </div>
  );
}
