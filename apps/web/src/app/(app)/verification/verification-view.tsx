"use client";

import Link from "next/link";

import { useMemo } from "react";

import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { dataOf } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseDomainGates, parseSignals, parseZeroCost } from "@/lib/verification/verification";

import { CoveragePanel } from "./coverage-panel";
import { fetchDomains, fetchZero, GatesPanel } from "./gates-panel";
import { ShadowPanel } from "./shadow-panel";
import { fetchSignals, SignalsPanel } from "./signals-panel";

/**
 * Verification: whether the data behind a conclusion is good enough to state it. A gate that
 * cannot be measured says so; absent coverage is shown as no coverage, never as zero.
 */
export function VerificationView() {
  const zero = useApiResource(fetchZero);
  const domains = useApiResource(fetchDomains);
  const signals = useApiResource(fetchSignals);
  const z = useMemo(() => (dataOf(zero.state) ? parseZeroCost(dataOf(zero.state)) : null), [zero.state]);
  const d = useMemo(() => (dataOf(domains.state) ? parseDomainGates(dataOf(domains.state)) : null), [domains.state]);
  const s = useMemo(() => (dataOf(signals.state) ? parseSignals(dataOf(signals.state)) : null), [signals.state]);
  const open = d ? d.domains.filter((g) => g.allowed).length : null;
  const openConclusions = d ? d.conclusions.filter((c) => c.allowed).length : null;
  const publishable = s ? s.filter((x) => x.publishable).length : null;
  const unknown = <span className="text-text-muted">Not measured</span>;
  const tiles: KpiTileProps[] = [
    {
      label: "Zero-cost audit",
      value: z ? (z.passed ? "Passed" : "Failed") : unknown,
      verdict: z
        ? { tone: z.passed ? "good" : "bad", text: z.passed ? "No paid endpoint is reachable." : "A paid endpoint is reachable. Nothing may go live." }
        : { tone: "unknown", text: "Still loading." },
    },
    {
      label: "Domains open",
      value: d ? `${open} of ${d.domains.length}` : unknown,
      verdict: d ? { tone: open === d.domains.length ? "good" : "caution", text: "Open when measured samples agree with the source." } : { tone: "unknown", text: "Still loading." },
    },
    {
      label: "Conclusions open",
      value: d ? `${openConclusions} of ${d.conclusions.length}` : unknown,
      verdict: d ? { tone: openConclusions === d.conclusions.length ? "good" : "caution", text: "Open when every domain they rest on is open." } : { tone: "unknown", text: "Still loading." },
    },
    {
      label: "Signals publishable",
      value: s ? `${publishable} of ${s.length}` : unknown,
      verdict: s ? { tone: publishable === s.length ? "good" : "caution", text: "Publishable after a reviewed audit shows accurate evidence." } : { tone: "unknown", text: "Still loading." },
    },
  ];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Verification"
        description="Whether the free sources are good enough to back each conclusion: the zero-cost audit, per-domain reconciliation gates, signal publish checks, measured coverage and the 30-day shadow report."
      />
      <KpiStrip tiles={tiles} label="Verification at a glance" />
      <GatesPanel zero={zero} domains={domains} z={z} d={d} />
      <SignalsPanel state={signals.state} signals={s ?? []} />
      <CoveragePanel />
      <ShadowPanel />
      <p className="text-xs text-text-muted">
        Related:{" "}
        <Link href="/data-sources" className="text-accent-ink hover:underline">
          Data sources
        </Link>{" "}
        ·{" "}
        <Link href="/activity" className="text-accent-ink hover:underline">
          Activity
        </Link>{" "}
        ·{" "}
        <Link href="/funds" className="text-accent-ink hover:underline">
          Funds
        </Link>
      </p>
    </div>
  );
}
