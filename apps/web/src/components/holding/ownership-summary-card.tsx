import Link from "next/link";

import { DataStateBadge } from "@/components/data-state";
import { sectionState } from "@/lib/holding-data/logic";
import type { EvidenceSummary } from "@/lib/holding-data/types";

import { day, HoldingPanel } from "./shared";

/**
 * Ownership, control and insider summary from the evidence read model. The three position
 * channels (13D/G control stakes, 13F institutions, N-PORT funds) are different measures and
 * are not added together; each shows its own coverage reason. Detail is on the X-ray page.
 */
export function OwnershipSummaryCard({ summary, ticker }: { summary: EvidenceSummary; ticker: string }) {
  const t = encodeURIComponent(ticker);
  const { control, institutional, funds } = summary.positions;
  const net = summary.insider_activity.activity.net_discretionary;
  const channels = [
    { id: "control", label: "Control stakes (13D/13G)", cov: control.coverage, figure: `${control.stakes.length} stake${control.stakes.length === 1 ? "" : "s"}`, note: control.stakes[0] ? `latest filed ${day(control.stakes[0].known_at)}` : null },
    { id: "inst", label: "Institutions (13F)", cov: institutional.coverage, figure: institutional.ownership ? `${institutional.ownership.top_holders.length} top holders listed` : "no holders listed", note: institutional.pit_note },
    { id: "funds", label: "Funds (N-PORT)", cov: funds.coverage, figure: `${funds.positions.positions.length} fund position${funds.positions.positions.length === 1 ? "" : "s"}`, note: null },
  ];
  return (
    <HoldingPanel
      id="ownership-control"
      kicker="Ownership, control and insiders"
      title="Who holds and who controls"
      takeaway={`${channels.filter((c) => c.cov.state === "covered").length} of 3 position channels are covered. ${summary.positions.not_additive}`}
      action={
        <span className="flex flex-wrap gap-3 text-xs">
          <Link href={`/xray?tab=ownership&ticker=${t}`} className="text-accent-ink hover:underline">Ownership in X-ray</Link>
          <Link href={`/xray?tab=insiders&ticker=${t}`} className="text-accent-ink hover:underline">Insiders in X-ray</Link>
        </span>
      }
      provenance={{
        source: "Stored Schedule 13D/G, Form 13F, Form N-PORT and Form 4 filings",
        asOf: null,
        knownAt: summary.known_as_of,
        method: "Channels are shown separately and never summed; insider net is derived from classified discretionary rows only.",
        state: channels.every((c) => c.cov.state === "covered") ? "verified" : channels.every((c) => c.cov.state === "not_covered") ? "not_covered" : "partial",
        reason: null,
        coverage: { covered: channels.filter((c) => c.cov.state === "covered").length, total: 3, unit: "position channels" },
      }}
    >
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {channels.map((c) => (
          <li key={c.id} className="min-w-0 border border-border-soft p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.label}</span>
              <DataStateBadge state={sectionState(c.cov)} reason={c.cov.reason} />
            </div>
            <p className="num mt-1">{c.figure}</p>
            {c.note ? <p className="text-xs text-text-muted">{c.note}</p> : null}
            <p className="text-xs text-text-muted">{c.cov.reason}</p>
          </li>
        ))}
      </ul>
      <div className="min-w-0 border border-border-soft p-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">Insider net discretionary</span>
          <DataStateBadge state={sectionState(summary.insider_activity.coverage)} reason={summary.insider_activity.coverage.reason} />
        </div>
        <p className="num mt-1">
          {net.buy_rows} buy row{net.buy_rows === 1 ? "" : "s"}, {net.sell_rows} sell row{net.sell_rows === 1 ? "" : "s"}, net {String(net.net_shares)} shares ({day(net.since)} to {day(net.until)}){net.value_complete ? "" : "; value incomplete"}
        </p>
        <p className="text-xs text-text-muted">Known by {day(summary.insider_activity.known_by)}. {net.plan_status_unknown_rows > 0 ? `${net.plan_status_unknown_rows} rows have unknown 10b5-1 plan status.` : ""}</p>
      </div>
    </HoldingPanel>
  );
}
