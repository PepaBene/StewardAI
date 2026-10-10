"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatFigure } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { fundHref, money, pct, signedPct, type CompareFund, type FundComparison } from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

function label(f: CompareFund): string {
  return f.name ?? f.isin;
}

function FundHeads({ funds }: { funds: CompareFund[] }) {
  return (
    <>
      {funds.map((f) => (
        <th key={f.isin} scope="col" className="min-w-36 px-2 py-1.5 text-right font-medium">
          <Link href={fundHref(f.isin)} className="hover:text-accent-ink hover:underline">{label(f)}</Link>
          <span className="block font-mono text-[11px] font-normal text-text-muted">{f.isin}</span>
          {f.duplicate_of ? <span className="block text-[11px] font-normal text-unconfirmed">same portfolio as {f.duplicate_of}</span> : null}
        </th>
      ))}
    </>
  );
}

export function CompareView() {
  const raw = useSearchParams().get("isins") ?? "";
  const isins = raw.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  const valid = isins.length >= 2 && isins.length <= 5 && isins.every((i) => ISIN.test(i));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Compare funds"
        description="Costs, overlap, exposure, concentration and returns side by side. Two share classes of one portfolio are shown but never compared with each other."
        aside={<Link href="/funds/research" className="text-accent-ink hover:underline">Choose other funds</Link>}
      />
      {!valid ? (
        <AlertMessage title="Choose two to five funds">
          Pick the funds on the <Link href="/funds/research" className="text-accent-ink underline">fund research</Link> page.
        </AlertMessage>
      ) : (
        <>
          <SaveComparison isins={isins} />
          <LoadedComparison isins={isins} />
        </>
      )}
    </div>
  );
}

/** Mounted only for a valid choice, so nothing is requested for one fund or a malformed address. */
function LoadedComparison({ isins }: { isins: readonly string[] }) {
  const joined = isins.join(",");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/compare", { params: { query: { isins: joined } } }), [joined]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="the comparison">
      {(c: FundComparison) => <Comparison c={c} />}
    </ResourceGate>
  );
}

function SaveComparison({ isins }: { isins: readonly string[] }) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    if (!name.trim()) return setError("Give the comparison a name.");
    setError(null);
    setBusy(true);
    const { error: err } = await getBrowserApiClient().POST("/fund-comparisons", { body: { name: name.trim(), isins: [...isins], note: note.trim() || null } });
    setBusy(false);
    if (err) return setError("The comparison could not be saved.");
    setSaved(true);
    setName("");
    setNote("");
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="w-64 border border-border bg-surface px-2 py-1.5" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-text-muted">Note (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} className="w-72 border border-border bg-surface px-2 py-1.5" />
        </label>
        <button type="submit" disabled={busy} className="border border-accent px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">Save this comparison</button>
        {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
        {saved ? (
          <p role="status" className="text-sm text-text-muted">
            Saved. It is listed on the <Link href="/funds/research" className="text-accent-ink hover:underline">fund research</Link> page; the figures are recomputed each time you open it.
          </p>
        ) : null}
      </form>
    </Blueprint>
  );
}

function Comparison({ c }: { c: FundComparison }) {
  const returnLabels = [...new Set(c.funds.flatMap((f) => f.returns.map((r) => r.label)))];
  return (
    <>
      {c.warnings.map((w) => (
        <AlertMessage key={w} title="Note">{w}</AlertMessage>
      ))}
      <Blueprint aria-labelledby="cmp-basic" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker="Issuer factsheets and holdings" title={<span id="cmp-basic">Costs, size and concentration</span>} />
        <div className="overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className="sticky left-0 min-w-44 bg-surface-muted px-3 py-1.5 text-left font-medium">Measure</th>
                <FundHeads funds={c.funds} />
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["Ongoing charge", (f: CompareFund) => pct(f.expense_ratio)],
                  ["Total assets", (f: CompareFund) => money(f.total_assets, f.total_assets_currency)],
                  ["Distribution", (f: CompareFund) => f.distribution_policy],
                  ["Holdings as of", (f: CompareFund) => (f.holdings_as_of ? `${formatDate(f.holdings_as_of)}${f.stale ? " (stale)" : ""}` : null)],
                  ["Top 10 issuers, share of net assets", (f: CompareFund) => pct(f.top10_weight, 1)],
                  ["Effective issuers", (f: CompareFund) => formatFigure(f.effective_issuers, "per_share")],
                  ["Weight not accounted for", (f: CompareFund) => pct(f.residual_weight)],
                  ["Equity without an ISIN", (f: CompareFund) => pct(f.unidentified_equity_weight)],
                ] as const
              ).map(([name, get]) => (
                <tr key={name} className="border-t border-border-soft">
                  <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-normal">{name}</th>
                  {c.funds.map((f) => {
                    const v = get(f);
                    return <td key={f.isin} className="num px-2 py-1 text-right">{v ?? <Absent reason="missing: not stored" />}</td>;
                  })}
                </tr>
              ))}
              {returnLabels.map((rl) => (
                <tr key={rl} className="border-t border-border-soft">
                  <th scope="row" className="sticky left-0 bg-surface px-3 py-1 text-left font-normal">Listing price return, {rl.toLowerCase()}</th>
                  {c.funds.map((f) => {
                    const r = f.returns.find((x) => x.label === rl);
                    return <td key={f.isin} className="num px-2 py-1 text-right">{signedPct(r?.value) ?? <Absent reason={r?.reason ? `missing: ${r.reason}` : "missing: no stored prices"} />}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-text-muted">Returns are for each fund&apos;s exchange listing in its own currency and are not NAV returns; choose a benchmark on a fund&apos;s Performance tab for a tracking difference.</p>
      </Blueprint>

      <Blueprint aria-labelledby="cmp-overlap" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="Sum over shared issuers of the smaller weight" title={<span id="cmp-overlap">Holdings overlap</span>} />
        {c.pairs.length === 0 ? (
          <p className="text-sm text-text-muted">There is no pair to compare.</p>
        ) : (
          c.pairs.map((p) => {
            const a = c.funds.find((f) => f.isin === p.fund_a);
            const b = c.funds.find((f) => f.isin === p.fund_b);
            return (
              <div key={`${p.fund_a}-${p.fund_b}`} className="space-y-2 border-t border-border-soft pt-3 first:border-t-0 first:pt-0">
                <p className="flex flex-wrap items-baseline gap-x-3 text-sm">
                  <span className="font-medium">{a ? label(a) : p.fund_a} and {b ? label(b) : p.fund_b}</span>
                  {p.skipped ? (
                    <Absent reason={`n/m: ${p.skipped}`} />
                  ) : p.overlap.value !== null && p.overlap.value !== undefined ? (
                    <span className="num text-lg font-semibold">{pct(p.overlap.value, 1)}</span>
                  ) : (
                    <Absent reason={p.overlap.warnings?.[0] ? `missing: ${p.overlap.warnings[0]}` : "missing: holdings unavailable"} />
                  )}
                  {!p.skipped ? <span className="text-xs text-text-muted">{p.shared_issuers} shared issuers</span> : null}
                </p>
                {p.overlap.warnings && p.overlap.value !== null && p.overlap.value !== undefined
                  ? p.overlap.warnings.map((w) => <p key={w} className="text-xs text-unconfirmed">{w}</p>)
                  : null}
                {p.shared.length > 0 ? (
                  <div className="overflow-x-auto border border-border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-surface-muted text-right">
                          <th scope="col" className="px-3 py-1 text-left font-medium">Shared issuer</th>
                          <th scope="col" className="px-2 py-1 font-medium">{a ? label(a) : p.fund_a}</th>
                          <th scope="col" className="px-2 py-1 font-medium">{b ? label(b) : p.fund_b}</th>
                          <th scope="col" className="px-2 py-1 font-medium">Overlap</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.shared.map((s) => (
                          <tr key={s.issuer} className="border-t border-border-soft">
                            <th scope="row" className="px-3 py-1 text-left font-normal">{s.issuer}</th>
                            <td className="num px-2 py-1 text-right">{pct(s.weight_a)}</td>
                            <td className="num px-2 py-1 text-right">{pct(s.weight_b)}</td>
                            <td className="num px-2 py-1 text-right font-semibold">{pct(s.overlap)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </Blueprint>

      <div className="grid gap-5 lg:grid-cols-2">
        {([["Sector", c.sectors, "cmp-sec"], ["Country", c.countries, "cmp-ctry"]] as const).map(([title, rows, id]) => (
          <Blueprint key={id} aria-labelledby={id} className="space-y-3 px-[22px] py-5">
            <PanelHeading kicker="Share of net assets, equity rows" title={<span id={id}>{title}</span>} />
            <div className="overflow-x-auto border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-surface-muted">
                    <th scope="col" className="px-3 py-1 text-left font-medium">{title}</th>
                    <FundHeads funds={c.funds} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.label} className={cn("border-t border-border-soft", r.kind !== "known" && "italic text-text-muted")}>
                      <th scope="row" className="px-3 py-1 text-left font-normal">{r.label}</th>
                      {c.funds.map((f) => (
                        <td key={f.isin} className="num px-2 py-1 text-right">{pct(r.weights[f.isin], 1) ?? <Absent reason="missing: holdings unavailable" />}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Blueprint>
        ))}
      </div>
      <p className="text-xs text-text-muted">Method {c.method_version}. Overlap uses each fund&apos;s own holdings date, so dates that differ are flagged above rather than aligned.</p>
    </>
  );
}
