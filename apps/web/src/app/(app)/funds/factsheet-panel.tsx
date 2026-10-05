"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { LineChart } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { OwnerTag } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { expenseSeries, ISIN_RE, parseFactsheets } from "@/lib/funds/funds";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { formatCompactMoney } from "@/lib/portfolio/money";
import { titleCase } from "@/lib/parse-untyped";

import { FactsheetEditor } from "./factsheet-editor";

const NOT_PUBLISHED = <span className="text-text-muted">Not published</span>;

/** Factsheet history of one fund: charges, benchmark, distribution policy and where each row came from. */
export function FactsheetPanel({ isin }: { isin: string | null }) {
  return (
    <Panel
      id="factsheet"
      title="Factsheet"
      description="Charges, benchmark and distribution policy as the issuer or you recorded them, newest first. A figure that was not published is shown as such, never as 0."
    >
      <IsinForm isin={isin} />
      {isin ? <FactsheetBody key={isin} isin={isin} /> : <EmptyState title="Pick a fund">Choose a fund in the overlap table above, or enter its ISIN.</EmptyState>}
    </Panel>
  );
}

function IsinForm({ isin }: { isin: string | null }) {
  const router = useRouter();
  const [text, setText] = useState(isin ?? "");
  const value = text.trim().toUpperCase();
  const valid = ISIN_RE.test(value);
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) router.push(`/funds?isin=${value}#factsheet`);
      }}
    >
      <div className="space-y-1">
        <Label htmlFor="fs-isin">Fund ISIN</Label>
        <Input id="fs-isin" value={text} onChange={(e) => setText(e.target.value)} placeholder="IE00B4L5Y983" className="num w-52" aria-invalid={text !== "" && !valid} />
      </div>
      <Button type="submit" size="sm" disabled={!valid}>
        Show factsheet
      </Button>
      {text !== "" && !valid ? <span className="pb-2 text-xs text-text-muted">An ISIN is 2 letters, 9 letters or digits and a check digit.</span> : null}
    </form>
  );
}

function FactsheetBody({ isin }: { isin: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/{isin}/factsheet", { params: { path: { isin }, query: { limit: 24 } } }),
    [isin],
  );
  const { state, reload } = useApiResource(fetcher);
  const data = dataOf(state);
  const rows = useMemo(() => (data ? parseFactsheets(data) : []), [data]);
  const series = useMemo(() => expenseSeries(rows), [rows]);
  const latest = rows[0] ?? null;

  return (
    <div className="space-y-4">
      <ResourceNotice state={state} what="factsheet" />
      <FactsheetEditor isin={isin} onSaved={reload} />
      {data && rows.length === 0 ? (
        <EmptyState title="No factsheet recorded for this fund">
          Neither the issuer feed nor a manual entry has produced one for {isin}.
        </EmptyState>
      ) : null}
      {latest ? (
        <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
            <Fact label="Fund ISIN" value={<span className="num">{isin}</span>} />
            <Fact label="Annual charge" value={latest.expenseRatio === null ? NOT_PUBLISHED : <span className="num">{formatPct(latest.expenseRatio, 2)} of fund value</span>} />
            <Fact label="Fund size" value={latest.totalAssets === null ? NOT_PUBLISHED : <span className="num">{formatCompactMoney(latest.totalAssets, latest.totalAssetsCurrency ?? "")}</span>} />
            <Fact label="Distribution" value={latest.distribution ? titleCase(latest.distribution) : NOT_PUBLISHED} />
            <Fact label="Benchmark" value={latest.benchmark ?? NOT_PUBLISHED} />
            <Fact label="NAV basis" value={latest.navBasis ?? NOT_PUBLISHED} />
            <Fact label="Share class" value={latest.shareClass ?? NOT_PUBLISHED} />
            <Fact
              label="As of"
              value={
                <span className="num">
                  {latest.asOf ?? "Unknown"} {latest.source === "manual" ? <OwnerTag>Entered by you</OwnerTag> : null}
                </span>
              }
            />
          </dl>
          {!latest.fieldNamesVerified ? (
            <p className="text-xs text-text-muted">The issuer payload field names for this row are unverified; check the figures against the issuer factsheet.</p>
          ) : null}
          {latest.warnings.length > 0 ? (
            <ul className="space-y-0.5 text-xs text-text-muted">
              {latest.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
          {series.length >= 2 ? (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Annual charge over time, % of fund value</h3>
              <LineChart data={series} area={false} formatValue={(v) => formatPct(v, 2)} label="Annual charge" ariaLabel="Annual charge of the fund over time" />
            </div>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <caption className="sr-only">Factsheet history</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">As of</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Annual charge</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Source</th>
                  <th scope="col" className="py-1.5 font-medium">Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.asOf}-${i}`} className="border-b border-border-soft">
                    <td className="num py-1.5 pr-3">{r.asOf ?? "Unknown"}</td>
                    <td className="num py-1.5 pr-3 text-right">{r.expenseRatio === null ? NOT_PUBLISHED : formatPct(r.expenseRatio, 2)}</td>
                    <td className="py-1.5 pr-3 text-xs text-text-muted">
                      {r.source === "manual" ? <OwnerTag>Entered by you</OwnerTag> : (r.source ?? "Unknown")}
                    </td>
                    <td className="py-1.5 text-xs text-text-muted">{r.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <p className="text-xs text-text-muted">
        <Link href="/funds#overlap" className="text-accent-ink hover:underline">Back to fund overlap</Link>
      </p>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-text-muted uppercase">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
