"use client";

import Link from "next/link";

import { SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";

import { CoveragePanel } from "./coverage-panel";
import { GatesPanel } from "./gates-panel";
import { ShadowPanel } from "./shadow-panel";
import { SignalsPanel } from "./signals-panel";

const SECTIONS = [
  { id: "gates", label: "Gates" },
  { id: "signals", label: "Signal publish checks" },
  { id: "coverage", label: "Measured coverage" },
  { id: "shadow", label: "Shadow report" },
] as const;

/**
 * Verification: whether the data behind a conclusion is good enough to state it. A gate that
 * cannot be measured says so; absent coverage is shown as no coverage, never as zero.
 */
export function VerificationView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="Verification"
        description="Whether the free sources are good enough to back each conclusion: the zero-cost audit, per-domain reconciliation gates, signal publish checks, measured coverage and the 30-day shadow report."
      />
      <SectionNav sections={SECTIONS} />
      <GatesPanel />
      <SignalsPanel />
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
