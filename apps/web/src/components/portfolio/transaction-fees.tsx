import type { FeeTotals, TransactionOut } from "@/lib/api/client";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatMoney } from "@/lib/format";
import { feeStatusNote } from "@/lib/portfolio/cash-flow";
import { feeName, rowFees } from "@/lib/portfolio/fees";
import { cn } from "@/lib/utils";

const BADGE_CLASS = { FX: "bg-accent-soft text-accent-ink", Tax: "bg-border-soft text-text" } as const;

/** A transaction row's Trading212 fees: the total with FX / Tax badges, the charges on hover. */
export function FeeCell({ row }: { row: TransactionOut }) {
  const fees = rowFees(row);
  if (fees.kind === "none") return <span className="text-text-faint">—</span>;
  if (fees.kind === "not_read") {
    return (
      <span className="text-xs text-text-faint" title="Stored before fee capture; the next sync re-reads Trading212's order history and fills this in.">
        not read yet
      </span>
    );
  }
  const detail = [...fees.lines, fees.inAmount ? "Already included in the amount." : "Charged on top of the amount."].join("\n");
  return (
    <span className="inline-flex items-center justify-end gap-1.5 whitespace-nowrap" title={detail}>
      {fees.badges.map((b) => (
        <span key={b} className={cn("px-1.5 py-px text-xs font-semibold", BADGE_CLASS[b])}>
          {b}
        </span>
      ))}
      <span className="num text-text-muted">{fees.currency ? formatMoney(fees.total, fees.currency) : fees.total}</span>
      <span className="sr-only">: {fees.lines.join("; ")}</span>
    </span>
  );
}

/** Fees over every row matching the table filters, in the base currency, with their source. */
export function FeeTotalsPanel({ fees }: { fees: FeeTotals }) {
  const cur = fees.currency;
  const status = feeStatusNote(fees);
  const rows: [string, number][] = [
    ["FX conversion", fees.fx_conversion],
    ["Taxes & levies", fees.taxes_levies],
    ["Account fees", fees.account_fees],
  ];
  return (
    <Blueprint aria-labelledby="fees-heading" className="flex min-w-0 flex-col gap-3 px-5.5 py-5">
      <PanelHeading kicker="Fees · table filters" title={<span id="fees-heading">FX &amp; fees</span>} />
      {fees.status === "not_captured" && fees.total === 0 ? (
        <p className="text-sm text-text-muted">{status}</p>
      ) : !cur ? (
        <p className="text-sm text-text-muted">No base currency yet: the first sync sets it.</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            <div className="min-w-0">
              <dt className="text-xs text-text-muted">Total</dt>
              <dd className="num font-heading text-[26px] leading-tight font-semibold">{formatMoney(fees.total, cur, { decimals: 0 })}</dd>
            </div>
            {rows.map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="text-xs text-text-muted">{label}</dt>
                <dd className="num text-base font-medium">{formatMoney(value, cur)}</dd>
              </div>
            ))}
          </dl>
          {fees.by_name.length ? (
            <ul className="space-y-1 text-[13px]">
              {fees.by_name.map((n) => (
                <li key={`${n.category}:${n.name}`} className="flex justify-between gap-3 border-b border-border-soft pb-1 last:border-0">
                  <span className="min-w-0 truncate text-text-muted">
                    {feeName(n.name)} <span className="num text-text-faint">× {n.count}</span>
                  </span>
                  <span className="num">{formatMoney(n.amount, cur)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-muted">No fees on the matching rows.</p>
          )}
          <p className="text-xs leading-snug text-text-faint">
            Trading212 per-order charges (walletImpact.taxes) and account FEE rows, each converted to {cur} at its day&rsquo;s{" "}
            {fees.fx_source === "ecb" ? "ECB" : "ČNB"} rate. {fees.fills_with_fees} of {fees.fills} orders carry a fee.
            {status ? ` ${status}` : ""}
            {fees.unconverted.length ? ` Fees in ${fees.unconverted.join(", ")} are left out (no reference rate stored).` : ""}
          </p>
        </>
      )}
    </Blueprint>
  );
}
