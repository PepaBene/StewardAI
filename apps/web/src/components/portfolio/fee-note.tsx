import type { FeeTotals } from "@/lib/api/client";
import { feeStatusNote, type CashFlowGraph } from "@/lib/portfolio/cash-flow";
import { formatCompactMoney } from "@/lib/portfolio/money";

/**
 * The cash-flow footnote's fee sentence: what the "FX & fees" node holds and where it comes
 * from, or honestly why it is missing (fees not read yet, or in a currency with no rate).
 */
export function CashFlowFeeNote({ fees, graph, currency }: { fees: FeeTotals | null; graph: CashFlowGraph; currency: string }) {
  if (!fees) return null;
  const status = feeStatusNote(fees);
  const other = fees.currency !== null && fees.currency !== currency;
  const parts = [
    fees.fx_conversion > 0 ? `FX conversion ${formatCompactMoney(fees.fx_conversion, currency)}` : null,
    fees.taxes_levies > 0 ? `taxes & levies ${formatCompactMoney(fees.taxes_levies, currency)}` : null,
    fees.account_fees > 0 ? `account fees ${formatCompactMoney(fees.account_fees, currency)}` : null,
  ].filter(Boolean);
  return (
    <>
      {graph.fees > 0 ? (
        <>
          {" "}
          FX &amp; fees {formatCompactMoney(graph.fees, currency)} ({parts.join(", ")}): Trading212 per-order charges, paid on top of the
          order amounts{fees.included_in_buys > 0 || fees.included_in_sales > 0 ? " (where an amount already held them, they are moved out of it)" : ""}.
        </>
      ) : fees.status === "captured" || fees.status === "partial" ? (
        other ? (
          <> Fees are summed in {fees.currency} and not drawn here.</>
        ) : (
          <> No Trading212 fees in this period.</>
        )
      ) : null}
      {status ? <> {status}</> : null}
      {fees.unconverted.length ? <> Fees in {fees.unconverted.join(", ")} are left out (no reference rate stored).</> : null}
    </>
  );
}
