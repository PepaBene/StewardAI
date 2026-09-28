import type { TransactionOut } from "@/lib/api/client";
import { formatMoney } from "@/lib/format";

/*
 * Trading212 fees on transaction rows (GET /portfolio/transactions `fees` per order row).
 * Amounts are already abs(): Trading212 reports the sign inconsistently.
 */

const FEE_NAMES: Record<string, string> = {
  CURRENCY_CONVERSION_FEE: "FX conversion fee",
  STAMP_DUTY: "Stamp duty",
  STAMP_DUTY_RESERVE_TAX: "Stamp duty reserve tax",
  FRENCH_TRANSACTION_TAX: "French transaction tax",
  FINRA_FEE: "FINRA fee",
  PTM_LEVY: "PTM levy",
  COMMISSION_TURNOVER: "Turnover commission",
  TRANSACTION_FEE: "Transaction fee",
  FEE: "Account fee",
};

/** "FRENCH_TRANSACTION_TAX" -> "French transaction tax"; unknown names are humanised. */
export function feeName(name: string): string {
  const known = FEE_NAMES[name];
  if (known) return known;
  const words = name.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type RowFees =
  | { kind: "none" }
  | { kind: "not_read" }
  | {
      kind: "fees";
      /** Sum of the charges in `currency` (charges in another currency are listed only). */
      total: number;
      currency: string | null;
      /** "FX" and/or "Tax" badges. */
      badges: ("FX" | "Tax")[];
      /** One line per charge, e.g. "FX conversion fee CZK 71.13". */
      lines: string[];
      /** Whether the order amount already includes them. */
      inAmount: boolean;
    };

/** What the fee cell of a transaction row shows. */
export function rowFees(row: Pick<TransactionOut, "type" | "currency" | "fees" | "fees_captured" | "fee_basis">): RowFees {
  if (row.type !== "order") return { kind: "none" };
  if (row.fees_captured === false) return { kind: "not_read" };
  const fees = (row.fees ?? []).filter((f) => f.amount > 0);
  if (fees.length === 0) return { kind: "none" };
  const currency = row.currency ?? fees[0].currency ?? null;
  const badges: ("FX" | "Tax")[] = [];
  if (fees.some((f) => f.category === "fx")) badges.push("FX");
  if (fees.some((f) => f.category === "tax")) badges.push("Tax");
  return {
    kind: "fees",
    total: fees.filter((f) => (f.currency ?? currency) === currency).reduce((s, f) => s + f.amount, 0),
    currency,
    badges,
    lines: fees.map((f) => {
      const cur = f.currency ?? currency;
      return `${feeName(f.name)} ${cur ? formatMoney(f.amount, cur) : String(f.amount)}`;
    }),
    inAmount: row.fee_basis === "included",
  };
}
