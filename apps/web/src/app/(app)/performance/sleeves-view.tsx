"use client";

import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import type { SleevesData } from "@/lib/performance/performance";

import { Formula, PlainReturn, Points } from "./shared";

const fetchSleeves = () => getBrowserApiClient().GET("/portfolio/performance/sleeves");

type SleeveCardData = NonNullable<SleevesData["core"]>;

function Sleeve({ title, sleeve }: { title: string; sleeve: SleeveCardData }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={sleeve.start && sleeve.end ? `${formatDate(sleeve.start)} to ${formatDate(sleeve.end)}` : undefined}
        title={title}
      />
      <dl className="mt-3 grid grid-cols-2 gap-4">
        <div>
          <dt className="text-xs text-text-muted">Return over the window</dt>
          <dd className="num text-2xl font-semibold">
            {sleeve.total_return === null || sleeve.total_return === undefined ? <Absent reason={`missing: ${sleeve.reason ?? "no return"}`} /> : <PlainReturn value={sleeve.total_return} />}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">A year</dt>
          <dd className="num text-2xl font-semibold">
            {sleeve.annualized === null || sleeve.annualized === undefined ? <span className="text-sm font-normal text-text-muted">under a year</span> : <PlainReturn value={sleeve.annualized} />}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-text-muted">
        {sleeve.tickers.length} {sleeve.tickers.length === 1 ? "holding" : "holdings"}: {sleeve.tickers.join(", ") || "none"}
      </p>
    </Blueprint>
  );
}

export function SleevesView() {
  const { state } = useApiResource(fetchSleeves);
  return (
    <ResourceGate state={state} label="core and stocks">
      {(data: SleevesData) =>
        !data.core || !data.stocks ? (
          <AlertMessage tone="info" title="Nothing to compare yet">
            {data.message}{" "}
            <Link href="/performance/ledger" className="text-accent-ink underline underline-offset-2">Open settings</Link>
          </AlertMessage>
        ) : (
          <div className="flex flex-col gap-5">
            {data.review_item ? (
              <AlertMessage title="Review item: your stocks are lagging the core">{data.message}</AlertMessage>
            ) : (
              <AlertMessage tone="info" title={data.too_early ? "Too early to judge" : "No review item"}>
                {data.message}
              </AlertMessage>
            )}
            <div className="grid gap-5 md:grid-cols-2">
              <Sleeve title="Core ETFs" sleeve={data.core} />
              <Sleeve title="Individual stocks" sleeve={data.stocks} />
            </div>
            <Blueprint className="px-[22px] py-4">
              <PanelHeading kicker={`Rolling window, after fees and currency costs`} title="The gap" />
              <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-text-muted">Core ahead of stocks</dt>
                  <dd className="num text-lg font-semibold"><Points value={data.annual_gap_points} /></dd>
                </div>
                <div><dt className="text-xs text-text-muted">Your limit</dt><dd className="num text-lg font-semibold">{data.gap_limit_points.toFixed(1)} pp a year</dd></div>
                <div><dt className="text-xs text-text-muted">History compared</dt><dd className="num text-lg font-semibold">{data.history_days} days</dd></div>
                <div>
                  <dt className="text-xs text-text-muted">Sleeves add up to the portfolio</dt>
                  <dd className="num text-lg font-semibold">
                    {data.reconciliation_error === null || data.reconciliation_error === undefined ? "—" : data.reconciliation_error < 1e-9 ? "Yes" : `Off by ${data.reconciliation_error.toExponential(1)}`}
                  </dd>
                </div>
              </dl>
              <p className="mt-2 max-w-2xl text-xs text-text-muted">
                A positive gap means the core ETFs did better. A gap is only called a finding after 12 months of history. Cash belongs to neither group. Which ETFs count as core is set under{" "}
                <Link href="/performance/ledger" className="text-accent-ink underline underline-offset-2">Ledger and settings</Link>.
              </p>
            </Blueprint>
            <Formula lines={data.explain.formula} version={data.explain.formula_version} />
          </div>
        )
      }
    </ResourceGate>
  );
}
