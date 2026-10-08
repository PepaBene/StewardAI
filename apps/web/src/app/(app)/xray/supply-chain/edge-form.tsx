"use client";

import { useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";

export const FIELD =
  "h-9 w-full border border-border bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";
export const BUTTON =
  "inline-flex h-9 items-center border border-border px-3 text-sm hover:bg-surface-muted disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

/** Add one supplier-to-customer link. Everything is typed by you: the source address and the date
 * say where and when you read it. */
export function EdgeForm({ onSaved }: { onSaved: () => void }) {
  const [supplier, setSupplier] = useState("");
  const [customer, setCustomer] = useState("");
  const [product, setProduct] = useState("");
  const [share, setShare] = useState("");
  const [source, setSource] = useState("");
  const [asOf, setAsOf] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    const percent = share.trim() === "" ? null : Number(share);
    if (percent !== null && (!Number.isFinite(percent) || percent <= 0 || percent > 100)) {
      return setProblem("The customer's share is a percent above 0 and up to 100, or leave it empty.");
    }
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/xray/inputs/supply-edges", {
      body: {
        supplier: supplier.trim(),
        customer: customer.trim(),
        product: product.trim(),
        customer_share: percent === null ? null : percent / 100,
        source_url: source.trim(),
        as_of: asOf,
      },
    });
    setBusy(false);
    if (!res.response.ok) {
      return setProblem("That link was not accepted. It needs two different tickers, what is supplied, a web address as the source and a date that is not in the future.");
    }
    setSupplier("");
    setCustomer("");
    setProduct("");
    setShare("");
    onSaved();
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Add a link" title="One company supplies another" />
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Supplier ticker</span>
          <input required className={FIELD} value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="TSM" maxLength={40} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Customer ticker</span>
          <input required className={FIELD} value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="AAPL" maxLength={40} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">What is supplied</span>
          <input required className={FIELD} value={product} onChange={(e) => setProduct(e.target.value)} placeholder="Processor chips" maxLength={120} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Customer&apos;s share of the supplier&apos;s revenue, %</span>
          <input className={FIELD} inputMode="decimal" value={share} onChange={(e) => setShare(e.target.value)} placeholder="Optional" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Where you read it</span>
          <input required type="url" className={FIELD} value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">As of</span>
          <input required type="date" className={FIELD} value={asOf} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setAsOf(e.target.value)} />
        </label>
        <div className="flex items-end">
          <button type="submit" disabled={busy} className={BUTTON}>Add this link</button>
        </div>
      </form>
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
    </Blueprint>
  );
}
