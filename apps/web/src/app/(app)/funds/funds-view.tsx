"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { ISIN_RE } from "@/lib/funds/funds";

import { AggregatePanel } from "./aggregate-panel";
import { CharacteristicsAuditPanel } from "./characteristics-audit-panel";
import { FactsheetPanel } from "./factsheet-panel";
import { LedgerPanel } from "./ledger-panel";
import { LookThroughPanel } from "./look-through-panel";
import { OverlapPanel } from "./overlap-panel";

const SECTIONS = [
  { id: "overlap", label: "Fund overlap" },
  { id: "aggregate", label: "Combined exposure" },
  { id: "look-through", label: "Look-through" },
  { id: "factsheet", label: "Factsheet" },
  { id: "ledger", label: "Where data comes from" },
  { id: "audit", label: "P/E and P/B audit" },
] as const;

/**
 * Funds: how much your funds duplicate each other, what you own once direct stocks and funds
 * are merged by issuer, and each fund's factsheet history. Weight a fund's rows do not account
 * for is shown as unknown and never spread over the known holdings.
 */
export function FundsView() {
  const raw = useSearchParams().get("isin")?.trim().toUpperCase() ?? "";
  const isin = ISIN_RE.test(raw) ? raw : null;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Funds"
        description="Overlap between your funds, your combined exposure by issuer, and each fund's factsheet. Unknown weight stays unknown."
      />
      <SectionNav sections={SECTIONS} />
      <OverlapPanel selectedIsin={isin} />
      <AggregatePanel />
      <LookThroughPanel />
      <FactsheetPanel isin={isin} />
      <LedgerPanel />
      <CharacteristicsAuditPanel />
      <p className="text-xs text-text-muted">
        Related:{" "}
        <Link href="/holdings" className="text-accent-ink hover:underline">
          Holdings
        </Link>{" "}
        ·{" "}
        <Link href="/data-sources" className="text-accent-ink hover:underline">
          Data sources
        </Link>{" "}
        ·{" "}
        <Link href="/verification" className="text-accent-ink hover:underline">
          Verification
        </Link>
      </p>
    </div>
  );
}
