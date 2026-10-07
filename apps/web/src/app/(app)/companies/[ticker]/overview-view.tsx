"use client";

import { CircleCheckIcon, CircleDashedIcon } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { useCompanyOverview } from "@/components/company/company-shell";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { companyHref, formatRatio, toNumber, type Overview, type RatiosView } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const KEY_RATIOS = ["revenue_cagr_3y", "operating_margin", "roic", "fcf_yield", "pe", "net_debt_to_ebitda"] as const;

function KeyRatios({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/ratios", { params: { path: { ticker }, query: { peers: "none" } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  return (
    <Blueprint aria-labelledby="key-ratios" className="space-y-3 px-[22px] py-5">
      <PanelHeading
        kicker="Latest fiscal year or TTM"
        title={<span id="key-ratios">Key ratios</span>}
        action={
          <Link href={companyHref(ticker, "ratios")} className="text-sm text-accent-ink hover:underline">
            All ratios and history
          </Link>
        }
      />
      <ResourceGate state={state} label="ratios">
        {(view: RatiosView) => {
          const rows = view.groups.flatMap((g) => g.ratios);
          return (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
              {KEY_RATIOS.map((id) => {
                const r = rows.find((x) => x.id === id);
                if (!r) return null;
                const text = formatRatio(r.latest?.value, r.unit);
                return (
                  <div key={id} className="min-w-0 border-l border-border pl-3">
                    <dt className="text-xs text-text-muted">{r.label}</dt>
                    <dd className="num mt-0.5 text-lg font-semibold">{text ?? <Absent reason={r.latest?.reason} />}</dd>
                    <p className="text-[11px] text-text-muted">{r.latest?.label}</p>
                  </div>
                );
              })}
            </dl>
          );
        }}
      </ResourceGate>
    </Blueprint>
  );
}

function Profile({ o }: { o: Overview }) {
  const get = (m: string) => o.profile.find((p) => p.metric === m);
  const desc = get("description");
  const facts: [string, string | undefined][] = [
    ["Legal name", o.legal_name ?? undefined],
    ["Sector", get("sector")?.value],
    ["Industry", get("industry")?.value],
    ["Employees", get("employees")?.value],
    ["Country", o.country ?? undefined],
    ["Identifiers", [o.cik && `CIK ${o.cik}`, o.lei && `LEI ${o.lei}`].filter(Boolean).join(" · ") || undefined],
  ];
  return (
    <Blueprint aria-labelledby="profile-heading" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Business" title={<span id="profile-heading">What the company is</span>} />
      {desc ? (
        <p className="max-w-[72ch] text-sm leading-relaxed">
          {desc.value}
          <span className="mt-1 block text-xs text-text-muted">
            Provider profile ({desc.provider}), read {formatDate(desc.known_at)}. <SourceLink href={desc.source_url}>Source</SourceLink>
          </span>
        </p>
      ) : (
        <p className="text-sm text-text-muted">No provider description is stored for this company. Sector, products and countries below come only from filings the app has read.</p>
      )}
      <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
        {facts.map(([k, v]) => (
          <div key={k} className="flex gap-3 border-b border-border-soft py-1 text-sm">
            <dt className="w-28 shrink-0 text-text-muted">{k}</dt>
            <dd className="min-w-0">{v ?? <Absent reason="not stored" />}</dd>
          </div>
        ))}
      </dl>
      {o.website ? (
        <p className="text-sm">
          <SourceLink href={o.website}>{o.website.replace(/^https?:\/\//, "")}</SourceLink>
        </p>
      ) : null}
    </Blueprint>
  );
}

function Segments({ o, ticker }: { o: Overview; ticker: string }) {
  const max = Math.max(...o.segments.map((s) => toNumber(s.value) ?? 0), 0);
  return (
    <Blueprint aria-labelledby="segments-heading" className="space-y-3 px-[22px] py-5">
      <PanelHeading
        kicker="Latest reported period"
        title={<span id="segments-heading">Revenue by segment</span>}
        action={
          <Link href={`/holdings/${encodeURIComponent(ticker)}`} className="text-sm text-accent-ink hover:underline">
            Full segment history
          </Link>
        }
      />
      {o.segments.length === 0 ? (
        <p className="text-sm text-text-muted">No dimensional segment revenue is stored. Segments appear once a filing with segment facts has been read.</p>
      ) : (
        <>
          <ul className="space-y-1.5">
            {o.segments.map((s) => {
              const v = toNumber(s.value) ?? 0;
              return (
                <li key={s.label} className="grid grid-cols-[minmax(8rem,14rem)_1fr_auto] items-center gap-3 text-sm">
                  <span className="truncate" title={s.label}>
                    {s.label}
                  </span>
                  <span className="h-2 bg-border-soft" aria-hidden="true">
                    <span className="block h-full bg-chart-line" style={{ width: `${max > 0 ? Math.max(2, (v / max) * 100) : 0}%` }} />
                  </span>
                  <span className="num text-right text-xs text-text-muted">{s.share !== null && s.share !== undefined ? `${(Number(s.share) * 100).toFixed(1)}%` : ""}</span>
                </li>
              );
            })}
          </ul>
          <p className="text-xs text-text-muted">
            Shares are of the listed lines only. Intersegment eliminations and unallocated revenue may sit outside them, so the shares are not guaranteed to sum to total revenue.
            {o.segments[0]?.period_end ? ` Period ending ${formatDate(o.segments[0].period_end)}.` : ""}
          </p>
        </>
      )}
    </Blueprint>
  );
}

function Context({ o }: { o: Overview }) {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Blueprint aria-labelledby="coverage-heading" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker="What the app knows" title={<span id="coverage-heading">Data coverage</span>} />
        <ul className="space-y-1.5 text-sm">
          {o.coverage.map((c) => (
            <li key={c.area} className="flex items-start gap-2">
              {c.present ? (
                <CircleCheckIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-gain" />
              ) : (
                <CircleDashedIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-text-faint" />
              )}
              <span>
                <span className="font-medium">{c.area}</span>
                <span className="sr-only">{c.present ? " available" : " missing"}</span>
                <span className="block text-xs text-text-muted">{c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      </Blueprint>
      <div className="space-y-5">
        <Blueprint aria-labelledby="upcoming-heading" className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker="Dated" title={<span id="upcoming-heading">Upcoming</span>} />
          {o.upcoming.length === 0 ? (
            <p className="text-sm text-text-muted">No upcoming results date or ex-dividend date is stored.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {o.upcoming.map((u) => (
                <li key={`${u.kind}-${u.date}`} className="flex gap-3">
                  <span className="num w-28 shrink-0">{formatDate(u.date)}</span>
                  <span>{u.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </Blueprint>
        <Blueprint aria-labelledby="rivals-heading" className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker="Confirmed only" title={<span id="rivals-heading">Competitors and peers</span>} />
          {o.competitors.length === 0 && o.peers_confirmed.length === 0 ? (
            <p className="text-sm text-text-muted">No competitor or peer has been confirmed. Suggested ones are reviewed under Relationship review, and peer sets can be chosen on the Peers tab.</p>
          ) : (
            <div className="space-y-2 text-sm">
              {o.competitors.length > 0 ? <p>Competitors: {o.competitors.join(", ")}.</p> : null}
              {o.peers_confirmed.length > 0 ? (
                <p className="flex flex-wrap gap-1.5">
                  Peers:
                  {o.peers_confirmed.map((p) => (
                    <Link key={p} href={companyHref(p)} className="border border-border px-1.5 font-mono text-xs hover:border-accent">
                      {p}
                    </Link>
                  ))}
                </p>
              ) : null}
            </div>
          )}
        </Blueprint>
      </div>
    </div>
  );
}

export function OverviewView({ ticker }: { ticker: string }) {
  const state = useCompanyOverview();
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="the company overview">
        {(o: Overview) => (
          <>
            <KeyRatios ticker={ticker} />
            <div className="grid gap-5 lg:grid-cols-[3fr_2fr]">
              <Profile o={o} />
              <Segments o={o} ticker={ticker} />
            </div>
            <Context o={o} />
          </>
        )}
      </ResourceGate>
    </div>
  );
}
