"use client";

import { QuoteIcon, SparklesIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Button } from "@/components/ui/button";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent, formatQuantity } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

/*
 * Company insight blocks (insight I13.3-I13.5): who owns the company by holder type, the chief
 * executive's pay and tenure with the management and board averages, and a cited AI note on the
 * stored earnings release. Each says what it covers and what it cannot know.
 */

type Mix = components["schemas"]["OwnershipMix"];
type Pay = components["schemas"]["PayAndTenure"];
type SummaryView = components["schemas"]["EarningsSummaryView"];
type Note = components["schemas"]["EarningsNote"];

const pct = (v: number | null | undefined, decimals = 1) => (v === null || v === undefined ? "—" : formatPercent(v, { decimals }));

// Fixed order, never cycled: institutions, insiders, the rest.
const MIX_COLOR: Record<Mix["mix"][number]["type"], string> = {
  institutions: "bg-categorical-1",
  insiders: "bg-categorical-2",
  public_other: "bg-categorical-3",
};

function MixBody({ d }: { d: Mix }) {
  if (d.status === "unavailable") {
    return <p className="text-sm text-text-muted">{d.reason}</p>;
  }
  const known = d.mix.filter((m) => m.share !== null && m.share !== undefined && m.share > 0);
  return (
    <div className="space-y-4">
      <div>
        <div className="flex h-4 w-full gap-0.5 bg-surface" role="img" aria-label={d.mix.map((m) => `${m.label} ${pct(m.share)}`).join(", ")}>
          {known.map((m) => (
            <span key={m.type} className={cn("h-full first:rounded-l-sm last:rounded-r-sm", MIX_COLOR[m.type])} style={{ width: `${(m.share ?? 0) * 100}%` }} />
          ))}
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {d.mix.map((m) => (
            <li key={m.type} className="flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-2.5 rounded-sm", MIX_COLOR[m.type])} />
              <span>{m.label}</span>
              <span className="num font-semibold">{pct(m.share)}</span>
              {m.holders ? <span className="text-xs text-text-muted">{m.holders} holders</span> : null}
            </li>
          ))}
        </ul>
        <p className="mt-1 text-xs text-text-muted">
          Of {d.shares_outstanding ? formatQuantity(d.shares_outstanding) : "—"} shares outstanding
          {d.shares_outstanding_as_of ? ` at ${formatDate(d.shares_outstanding_as_of)}` : ""}
          {d.period ? `; 13F period ${formatDate(d.period)}` : ""}.
        </p>
      </div>
      {d.institutions.length > 0 ? (
        <div className="max-h-96 overflow-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Largest institutional holders</caption>
            <thead>
              <tr className="sticky top-0 bg-surface-muted text-left text-xs">
                <th scope="col" className="px-2 py-1.5 font-medium">Holder</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Shares</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Of the company</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Change</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Of their own book</th>
              </tr>
            </thead>
            <tbody>
              {d.institutions.map((r) => (
                <tr key={r.cik} className="border-t border-border-soft">
                  <th scope="row" className="px-2 py-1 text-left font-normal">{r.name ?? r.cik}</th>
                  <td className="num px-2 py-1 text-right">{formatQuantity(r.shares)}</td>
                  <td className="num px-2 py-1 text-right">{pct(r.share_of_outstanding, 2)}</td>
                  <td className={cn("num px-2 py-1 text-right", (r.change_pct ?? 0) > 0 ? "text-gain" : (r.change_pct ?? 0) < 0 ? "text-loss" : "")}>
                    {r.change_pct === null || r.change_pct === undefined ? "new or —" : formatPercent(r.change_pct, { signed: true, decimals: 1 })}
                  </td>
                  <td className="num px-2 py-1 text-right">{r.portfolio_share === null || r.portfolio_share === undefined ? <span className="text-text-muted">—</span> : pct(r.portfolio_share, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {d.insiders.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-sm text-accent-ink">Officers and directors who hold shares ({d.insiders.length})</summary>
          <ul className="mt-2 divide-y divide-border-soft text-sm">
            {d.insiders.map((i) => (
              <li key={`${i.name}-${i.as_of}`} className="flex flex-wrap justify-between gap-x-3 py-1">
                <span>{i.name} <span className="text-xs text-text-muted">{i.role}</span></span>
                <span className="num">{formatQuantity(i.shares)} · {pct(i.share_of_outstanding, 2)} <span className="text-xs text-text-muted">at {formatDate(i.as_of)}</span></span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
        {d.coverage.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </div>
  );
}

export function OwnershipMixPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/ownership-mix", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <Blueprint aria-labelledby="own-mix" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Institutions, insiders and everyone else" title={<span id="own-mix">Who owns the company</span>} />
      <ResourceGate state={state} label="the ownership mix">
        {(d: Mix) => <MixBody d={d} />}
      </ResourceGate>
    </Blueprint>
  );
}

function money(v: number, currency: string) {
  return formatMoney(v, currency, { decimals: 0 });
}

function PayBody({ d }: { d: Pay }) {
  if (d.status === "unavailable") return <p className="text-sm text-text-muted">{d.reason}</p>;
  const headline = d.pay[0];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-text-muted">Chief executive</dt>
          <dd className="text-sm font-semibold">{d.ceo_name ?? <Absent reason="not identified" />}{d.ceo_title ? <span className="block text-xs font-normal text-text-muted">{d.ceo_title}</span> : null}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Pay{d.pay_fiscal_year ? `, ${d.pay_fiscal_year}` : ""}</dt>
          <dd className="num text-lg font-semibold">{headline ? money(headline.value, headline.currency) : "—"}</dd>
          {d.salary_share !== null && d.salary_share !== undefined ? <dd className="text-xs text-text-muted">{pct(d.salary_share, 0)} as salary</dd> : null}
        </div>
        <div>
          <dt className="text-xs text-text-muted">In the role</dt>
          <dd className="num text-lg font-semibold">{d.ceo_tenure_years === null || d.ceo_tenure_years === undefined ? "—" : `${d.ceo_tenure_years.toFixed(1)} years`}</dd>
          {d.ceo_since ? <dd className="text-xs text-text-muted">since {formatDate(d.ceo_since)}</dd> : null}
        </div>
        <div>
          <dt className="text-xs text-text-muted">Owns</dt>
          <dd className="num text-lg font-semibold">{pct(d.ceo_share_of_outstanding, 2)}</dd>
          {d.ceo_shares ? <dd className="text-xs text-text-muted">{formatQuantity(d.ceo_shares)} shares{d.ceo_ownership_as_of ? ` at ${formatDate(d.ceo_ownership_as_of)}` : ""}</dd> : null}
        </div>
      </dl>
      {d.pay.length > 0 ? (
        <ul className="divide-y divide-border-soft text-sm">
          {d.pay.map((p) => (
            <li key={`${p.label}-${p.source}`} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1">
              <span>{p.label}{p.fiscal_year ? `, ${p.fiscal_year}` : ""}</span>
              <span className="num font-semibold">{money(p.value, p.currency)}</span>
              <span className="w-full text-xs text-text-muted">{p.source} <SourceLink href={p.source_url}>filing</SourceLink></span>
            </li>
          ))}
        </ul>
      ) : null}
      {d.ceo_tenure_basis ? <p className="text-xs text-text-muted">Tenure: {d.ceo_tenure_basis}.</p> : null}
      <div className="grid gap-4 sm:grid-cols-2">
        {[
          { title: "Management", avg: d.management_avg_tenure, people: d.management },
          { title: "Board", avg: d.board_avg_tenure, people: d.board },
        ].map((g) => (
          <div key={g.title}>
            <p className="text-sm">
              <span className="font-semibold">{g.title}</span>{" "}
              <span className="num">{g.avg === null || g.avg === undefined ? "—" : `${g.avg.toFixed(1)} years on average`}</span>
              <span className="text-xs text-text-muted"> · {g.people.length} people</span>
            </p>
            {g.people.length > 0 ? (
              <ul className="mt-1 space-y-0.5 text-xs">
                {g.people.slice(0, 12).map((p) => (
                  <li key={p.name} className="flex justify-between gap-2">
                    <span>{p.name}</span>
                    <span className="num text-text-muted">{p.years.toFixed(1)} y</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </div>
      {d.caveats.map((c) => (
        <p key={c} className="text-xs text-text-muted">{c}</p>
      ))}
    </div>
  );
}

export function PayTenurePanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/pay-tenure", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <Blueprint aria-labelledby="gov-ceo" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Proxy statement and Form 4" title={<span id="gov-ceo">Chief executive: pay, tenure and stake</span>} />
      <ResourceGate state={state} label="pay and tenure">
        {(d: Pay) => <PayBody d={d} />}
      </ResourceGate>
    </Blueprint>
  );
}

function Cited({ line, ask = false }: { line: Note["summary"][number]; ask?: boolean }) {
  return (
    <li className="space-y-1">
      <p className={cn("text-sm leading-snug", ask && "font-medium")}>{line.text}</p>
      <p className="flex items-start gap-1.5 text-xs text-text-muted">
        <QuoteIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
        {line.quote_url ? (
          <a href={line.quote_url} target="_blank" rel="noreferrer" className="hover:text-accent-ink hover:underline">
            “{line.quote}”
          </a>
        ) : (
          <span>“{line.quote}”</span>
        )}
      </p>
    </li>
  );
}

function NoteBody({ v, ticker, onWritten }: { v: SummaryView; ticker: string; onWritten: (n: Note) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data, error: err, response } = await getBrowserApiClient().POST("/companies/{ticker}/earnings-summary", { params: { path: { ticker } } });
      if (data) onWritten(data);
      else {
        const detail = (err as { detail?: unknown } | undefined)?.detail;
        setError(typeof detail === "string" ? detail : `The API answered HTTP ${response.status}.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const n = v.note;
  return (
    <div className="space-y-4">
      {!v.latest_release ? <p className="text-sm text-text-muted">{v.reason}</p> : null}
      {v.latest_release ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant={n && !v.stale ? "outline" : "default"} onClick={() => void write()} disabled={busy}>
            <SparklesIcon aria-hidden="true" />
            {busy ? "Reading the release…" : n ? (v.stale ? "Summarise the newer release" : "Write the note again") : "Summarise the release"}
          </Button>
          <span className="text-xs text-text-muted">
            Latest release{v.latest_release.period ? `, ${v.latest_release.period}` : ""}
            {v.latest_release.fetched_at ? `, read ${formatDate(v.latest_release.fetched_at)}` : ""} <SourceLink href={v.latest_release.source_url}>source</SourceLink>
          </span>
        </div>
      ) : null}
      {error ? <AlertMessage title="No note written">{error}</AlertMessage> : null}
      {n ? (
        <>
          {v.stale ? <AlertMessage tone="info" title="A newer release is stored">This note was written from an earlier release.</AlertMessage> : null}
          <div>
            <h4 className="text-xs font-medium text-text-muted">Summary{n.period ? `, ${n.period}` : ""}</h4>
            <ul className="mt-2 space-y-3">
              {n.summary.map((s) => (
                <Cited key={s.text} line={s} />
              ))}
            </ul>
          </div>
          {n.questions.length > 0 ? (
            <div>
              <h4 className="text-xs font-medium text-text-muted">Questions to ask</h4>
              <ul className="mt-2 space-y-3">
                {n.questions.map((q) => (
                  <Cited key={q.text} line={q} ask />
                ))}
              </ul>
            </div>
          ) : null}
          <p className="text-xs text-text-muted">
            {n.note}
            {n.dropped > 0 ? ` ${n.dropped} line${n.dropped === 1 ? " was" : "s were"} dropped because the quote is not in the release.` : ""}
            {n.created_at ? ` Written ${formatDate(n.created_at)}` : ""}{n.model ? ` by ${n.model}` : ""}; method {n.method_version}.
          </p>
        </>
      ) : null}
    </div>
  );
}

export function EarningsSummaryPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/earnings-summary", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  const [written, setWritten] = useState<Note | null>(null);
  return (
    <Blueprint aria-labelledby="earn-note" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="AI note, every line quoted from the release" title={<span id="earn-note">Latest results in brief</span>} />
      <ResourceGate state={state} label="the earnings note">
        {(v: SummaryView) => (
          <NoteBody
            v={written ? { ...v, note: written, stale: v.latest_release ? written.evidence_sha256 !== v.latest_release.evidence_sha256 : false } : v}
            ticker={ticker}
            onWritten={setWritten}
          />
        )}
      </ResourceGate>
    </Blueprint>
  );
}
