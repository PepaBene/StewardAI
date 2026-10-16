"use client";

import Link from "next/link";
import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { Problem, problemText } from "@/components/risk/common";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { auditState, STATUS_LABEL, type FeatureReadiness, type Readiness } from "@/lib/release/release";
import { cn } from "@/lib/utils";

const fetchReadiness = () => getBrowserApiClient().GET("/release/readiness");
const fetchActivation = () => getBrowserApiClient().GET("/release/activation");

export function ReadinessView() {
  const { state } = useApiResource(fetchReadiness);
  const { state: activation, reload } = useApiResource(fetchActivation);
  // Unknown is not off: until the switches have loaded no feature shows a state or a button.
  const on = activation.kind === "ok" ? new Set(activation.data.filter((a) => a.active).map((a) => a.feature)) : null;
  return (
    <div className="flex flex-col gap-5">
      {activation.kind === "error" ? (
        <p role="alert" className="rounded-control border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          The switches could not be loaded ({activation.title}), so which features are on is unknown and none can be changed.{" "}
          <button type="button" className="underline underline-offset-2" onClick={() => reload()}>
            Try again
          </button>
        </p>
      ) : null}
      <ResourceGate state={state} label="the release checks">
        {(readiness) => <Body readiness={readiness} on={on} onChanged={reload} />}
      </ResourceGate>
    </div>
  );
}

/** Switch a feature on or off. The server refuses a switch-on the gate does not allow and says why. */
function SwitchButton({ feature, isOn, onChanged }: { feature: FeatureReadiness; isOn: boolean | null; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function toggle() {
    if (isOn === null) return;
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/release/activation", { body: { feature: feature.id, active: !isOn } });
    setBusy(false);
    if (!res.response.ok) return setProblem(problemText(res.error, "That could not be saved."));
    onChanged();
  }
  const blocked = !isOn && !feature.can_activate;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        type="button"
        size="sm"
        variant={isOn ? "outline" : "default"}
        disabled={busy || blocked || isOn === null}
        onClick={() => void toggle()}
        aria-label={`${isOn ? "Switch off" : "Switch on"} ${feature.title}`}
      >
        {busy ? "Saving…" : isOn ? "Switch off" : "Switch on"}
      </Button>
      <Problem>{problem}</Problem>
    </div>
  );
}

function featureRow(f: FeatureReadiness, zeroCostPassed: boolean, isOn: boolean | null, onChanged: () => void): StatusRow {
  const status = STATUS_LABEL[f.status];
  const audit = auditState(f);
  const failingWhileOn = isOn === true && !f.can_activate;
  const sentence = failingWhileOn
    ? `On, but it no longer passes its checks. Switch it off. ${f.reason}`
    : f.can_activate
      ? isOn
        ? "On. Free access, source audit and zero-cost audit all pass."
        : "May be switched on: free access, source audit and zero-cost audit all pass."
      : `Stays off. ${f.reason} Source audit: ${audit.label.toLowerCase()}.`;
  return {
    id: f.id,
    state: f.can_activate && !failingWhileOn ? "pass" : "fail",
    lead: f.title,
    sentence,
    action: <SwitchButton feature={f} isOn={isOn} onChanged={onChanged} />,
    details: <FeatureDetail feature={f} statusLabel={status.label} zeroCostPassed={zeroCostPassed} />,
  };
}

function Body({ readiness, on, onChanged }: { readiness: Readiness; on: Set<string> | null; onChanged: () => void }) {
  const total = readiness.features.length;
  const audited = readiness.features.reduce((n, f) => n + f.audited_facts, 0);
  const rows = readiness.features.map((f) => featureRow(f, readiness.zero_cost_passed, on === null ? null : on.has(f.id), onChanged));
  return (
    <div className="flex flex-col gap-5">
      <KpiStrip
        label="Release readiness at a glance"
        tiles={[
          {
            label: "Ready to release",
            value: readiness.zero_cost_passed && readiness.activatable === total ? "Yes" : readiness.activatable > 0 ? "Partly" : "No",
            verdict: {
              tone: readiness.activatable === total ? "good" : "caution",
              text: readiness.activatable === total ? "Every feature may be switched on." : "Some features stay off until their checks pass.",
            },
          },
          {
            label: "May be switched on",
            value: `${readiness.activatable} of ${total}`,
            verdict: { tone: readiness.activatable > 0 ? "good" : "caution", text: "A feature needs three passing checkpoints." },
          },
          {
            label: "Inputs in place",
            value: `${readiness.ready} of ${total}`,
            verdict: { tone: readiness.ready === total ? "good" : "caution", text: "Reachable on the free tier and holding data." },
          },
          {
            label: "Zero-cost audit",
            value: readiness.zero_cost_passed ? "Passed" : "Failed",
            note: `${audited} facts matched to a source`,
            verdict: {
              tone: readiness.zero_cost_passed ? "good" : "bad",
              text: readiness.zero_cost_passed
                ? "No endpoint can spend money."
                : `${readiness.zero_cost_violations} violation${readiness.zero_cost_violations === 1 ? "" : "s"}. Nothing may be switched on yet.`,
            },
          },
        ]}
      />
      <section aria-labelledby="features-heading" className="blueprint space-y-2 p-5">
        <div>
          <h2 id="features-heading" className="text-[22px] leading-tight font-semibold">
            Features and their checkpoints
          </h2>
          <p className="text-sm text-text-muted">
            A feature passes three checkpoints: its inputs are reachable on the free tier and hold data, a sample of its facts matches the source, and
            the zero-cost audit passes. Open Details for each checkpoint.
          </p>
        </div>
        <StatusList rows={rows} caption="Features and whether they may be switched on" passWord="may be on" />
      </section>
    </div>
  );
}

function FeatureDetail({ feature, statusLabel, zeroCostPassed }: { feature: FeatureReadiness; statusLabel: string; zeroCostPassed: boolean }) {
  const audit = auditState(feature);
  return (
    <div className="space-y-2 text-sm text-text">
      <p className="text-xs text-text-muted">Plan {feature.plan}</p>
      <dl className="grid gap-px overflow-hidden rounded-control bg-border-soft sm:grid-cols-3">
        <Checkpoint term="Free access" passed={feature.status === "ready"} value={statusLabel} note={feature.reason} />
        <Checkpoint
          term="Source audit"
          passed={audit.passed}
          value={audit.passed ? "Passed" : feature.audit === "mismatch" ? "Failed" : "Open"}
          note={audit.label}
          action={
            <Link href="/release/audit" className="underline underline-offset-2">
              Compare facts
            </Link>
          }
        />
        <Checkpoint
          term="Zero cost"
          passed={zeroCostPassed}
          value={zeroCostPassed ? "Passed" : "Failed"}
          note={zeroCostPassed ? "No paid endpoint is reachable." : "The zero-cost audit has violations."}
        />
      </dl>
      {feature.limit ? <p className="text-xs text-text-muted">Known limit: {feature.limit}</p> : null}
      {feature.inputs.length > 0 ? (
        <ul className="space-y-0.5 text-xs">
          {feature.inputs.map((i) => (
            <li key={i.label}>
              <span className="font-medium">{i.label}</span>{" "}
              <span className="text-text-muted">
                ({i.kind === "store" ? "stored data" : "free-tier endpoint"}, {i.required ? "core" : "extra"}): {i.ok ? "OK" : "Not OK"}, {i.detail}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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
        <span aria-hidden="true" className={cn("size-2.5 rounded-full border", passed ? "border-info-signal bg-info-signal" : "border-dashed border-text-muted")} />
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
