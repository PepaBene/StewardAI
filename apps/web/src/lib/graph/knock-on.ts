/**
 * View model for `/portfolio/relationship-knock-on` (untyped): confirmed, dated customer /
 * supplier links between two open positions. Shares are as reported by the filer and are never
 * summed or imputed; a link without a clear direction stays out of the flow diagram.
 */
import { arr, isRecord, num, recs, str, titleCase } from "@/lib/parse-untyped";

export type KnockOnLink = {
  /** The company that sells (supplier) and the one that buys (customer). Null when direction is unclear. */
  supplier: string | null;
  customer: string | null;
  holder: string;
  counterparty: string;
  relationship: string;
  direction: string | null;
  reportedShare: number | null;
  shareBasis: string | null;
  fiscalPeriod: string | null;
  source: string | null;
};

export type KnockOn = { links: KnockOnLink[]; limitations: string[]; knownAsOf: string | null };

export function parseKnockOn(payload: unknown): KnockOn | null {
  if (!isRecord(payload)) return null;
  const links = recs(payload.links)
    .map((l): KnockOnLink | null => {
      const holder = str(l.ticker);
      const counterparty = str(l.counterparty_ticker);
      if (!holder || !counterparty) return null;
      const direction = str(l.direction);
      let supplier: string | null = null;
      let customer: string | null = null;
      if (direction === "issuer_sells_to") [supplier, customer] = [holder, counterparty];
      else if (direction === "issuer_buys_from") [supplier, customer] = [counterparty, holder];
      return {
        supplier,
        customer,
        holder,
        counterparty,
        relationship: str(l.relationship) ?? "link",
        direction,
        reportedShare: num(l.reported_share),
        shareBasis: str(l.share_basis),
        fiscalPeriod: str(l.fiscal_period),
        source: str(l.source),
      };
    })
    .filter((l): l is KnockOnLink => l !== null);
  return {
    links,
    limitations: arr(payload.limitations).filter((x): x is string => typeof x === "string"),
    knownAsOf: str(payload.known_as_of),
  };
}

export function directionLabel(l: KnockOnLink): string {
  if (l.supplier && l.customer) return `${l.supplier} supplies ${l.customer}`;
  return `${titleCase(l.relationship)} (direction unclear)`;
}

/** Sankey inputs: suppliers on the left, customers on the right (a company on both sides appears twice). One unit of flow per link. */
export function knockOnSankey(links: readonly KnockOnLink[]) {
  const directed = links.filter((l) => l.supplier && l.customer);
  const nodes = new Map<string, { id: string; label: string; column: number }>();
  const flows = new Map<string, { source: string; target: string; value: number }>();
  for (const l of directed) {
    const s = `s:${l.supplier}`;
    const c = `c:${l.customer}`;
    nodes.set(s, { id: s, label: l.supplier!, column: 0 });
    nodes.set(c, { id: c, label: l.customer!, column: 1 });
    const f = flows.get(`${s}>${c}`);
    if (f) f.value += 1;
    else flows.set(`${s}>${c}`, { source: s, target: c, value: 1 });
  }
  return { nodes: [...nodes.values()], links: [...flows.values()], unclear: links.length - directed.length };
}

/** How many held companies each holding feeds or depends on; null counts are not produced (a holding with no link is absent). */
export function linkCounts(links: readonly KnockOnLink[]): { ticker: string; asSupplier: number; asCustomer: number }[] {
  const m = new Map<string, { ticker: string; asSupplier: number; asCustomer: number }>();
  const row = (t: string) => m.get(t) ?? m.set(t, { ticker: t, asSupplier: 0, asCustomer: 0 }).get(t)!;
  for (const l of links) {
    if (l.supplier) row(l.supplier).asSupplier += 1;
    if (l.customer) row(l.customer).asCustomer += 1;
  }
  return [...m.values()].sort((a, b) => b.asSupplier + b.asCustomer - (a.asSupplier + a.asCustomer) || a.ticker.localeCompare(b.ticker));
}
