import { formatMoney, formatPercent } from "@/lib/format";
import type { components } from "@/lib/api/schema";

import { TD, TH, TH_LEFT } from "@/app/(app)/xray/shared";

type Proposal = components["schemas"]["XrayProposal"];
type Side = Proposal["before"];

type Row = { label: string; key: keyof Side; kind: "pct" | "num" };

const ROWS: Row[] = [
  { label: "Cash", key: "cash_weight", kind: "pct" },
  { label: "Equity identified", key: "equity_weight", kind: "pct" },
  { label: "Unknown fund weight", key: "unknown_weight", kind: "pct" },
  { label: "Ten largest issuers", key: "top10_weight", kind: "pct" },
  { label: "Effective number of issuers", key: "effective_issuers", kind: "num" },
  { label: "Redundant exposure", key: "redundant_weight", kind: "pct" },
  { label: "Fund fees a year", key: "fee_drag", kind: "pct" },
];

function show(kind: Row["kind"], v: unknown): string {
  if (typeof v !== "number") return "—";
  return kind === "pct" ? formatPercent(v, { decimals: 1 }) : v.toFixed(1);
}

/** The X-Ray measures that matter for a decision, before and after, with what changed. The full lens view stays on the X-Ray page. */
export function ProposalSummary({ proposal }: { proposal: Proposal }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-text-muted">
        Total held {formatMoney(proposal.before.total_value, proposal.base_currency, { decimals: 0 })} now,{" "}
        {formatMoney(proposal.after.total_value, proposal.base_currency, { decimals: 0 })} after. Your saved holdings are unchanged.
      </p>
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">X-Ray measures before and after</caption>
          <thead>
            <tr className="bg-surface-muted">
              <th scope="col" className={TH_LEFT}>Measure</th>
              <th scope="col" className={TH}>Now</th>
              <th scope="col" className={TH}>After</th>
              <th scope="col" className={TH}>Change</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r) => {
              const b = proposal.before[r.key];
              const a = proposal.after[r.key];
              const delta = typeof a === "number" && typeof b === "number" ? a - b : null;
              const text =
                delta === null
                  ? "—"
                  : r.kind === "pct"
                    ? `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta * 100).toFixed(1)} pp`
                    : `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta).toFixed(1)}`;
              return (
                <tr key={r.key} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">{r.label}</th>
                  <td className={TD}>{show(r.kind, b)}</td>
                  <td className={TD}>{show(r.kind, a)}</td>
                  <td className={TD}>{text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {proposal.coverage.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
          {proposal.coverage.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
