"use client";

import Link from "next/link";
import { Children, useCallback, useState } from "react";

import { XIcon } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { EntityName } from "@/components/entity-name";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { PageHeader } from "@/components/page-header";
import { FilterChips } from "@/components/ui/filter-chips";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseAiDecisions, totalDecisions } from "@/lib/reference/ai-decisions";
import { SLOT_LABELS, displayValue } from "@/lib/reference/web-facts";
import { cn } from "@/lib/utils";

const WINDOWS = [7, 14, 30] as const;

/** One AI decision with a one-click overturn. `run` returns true when the change was saved. */
function Row({ ticker, children, undo }: { ticker: string; children: React.ReactNode; undo: () => Promise<boolean> }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  async function overturn() {
    setState("busy");
    setState((await undo()) ? "done" : "failed");
  }
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 py-2 text-sm">
      <div className="min-w-0 space-y-0.5">
        <Link href={`/companies/${encodeURIComponent(ticker)}`} className="font-medium text-accent-ink hover:underline">
          <EntityName id={ticker} chip={false} />
        </Link>{" "}
        {children}
      </div>
      <div className="flex items-center gap-2">
        {state === "done" ? <span className="text-xs text-text-muted">Rejected</span> : null}
        {state === "failed" ? <span className="text-xs text-danger">Could not save</span> : null}
        {state === "idle" || state === "failed" || state === "busy" ? (
          <Button size="sm" variant="outline" disabled={state === "busy"} onClick={overturn}>
            Reject
          </Button>
        ) : null}
      </div>
    </li>
  );
}

/** A period as written ("FY2025", "H1 2026"), or a formatted date when it is an ISO date. */
function periodWords(period: string | null | undefined): string {
  if (!period) return "";
  return /^\d{4}-\d{2}-\d{2}$/.test(period) ? formatDate(period) : period;
}

type Exposure = ReturnType<typeof parseAiDecisions>["exposures"][number];

/** One input or currency inside a group, with its own reject. */
function ExposureChip({ x, undo }: { x: Exposure; undo: () => Promise<boolean> }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  if (state === "done") return <span className="rounded-mark bg-surface px-2 py-0.5 text-xs text-text-muted line-through">{x.name}</span>;
  return (
    <span className="inline-flex items-center gap-1 rounded-mark bg-surface px-2 py-0.5 text-xs">
      {x.name}
      {x.share !== null ? <span className="num text-text-muted">{(x.share * 100).toFixed(0)}%</span> : null}
      <button
        type="button"
        disabled={state === "busy"}
        onClick={async () => {
          setState("busy");
          setState((await undo()) ? "done" : "failed");
        }}
        aria-label={`Reject ${x.name}`}
        title={state === "failed" ? "Could not save; try again" : `Reject ${x.name}`}
        className={cn("rounded-mark px-1 text-text-muted hover:bg-surface-muted hover:text-text", state === "failed" && "text-loss")}
      >
        <XIcon aria-hidden="true" className="size-3" />
      </button>
    </span>
  );
}

/** Exposures sharing a company, kind and quote: the quote once, every input it supports as a chip. */
export function groupExposures(list: readonly Exposure[]): { key: string; ticker: string; kind: string; period: string; quote: string | null; items: Exposure[] }[] {
  const out = new Map<string, { key: string; ticker: string; kind: string; period: string; quote: string | null; items: Exposure[] }>();
  for (const x of list) {
    const key = `${x.ticker}|${x.kind}|${x.quote ?? ""}`;
    const g = out.get(key) ?? { key, ticker: x.ticker, kind: x.kind, period: x.period ?? "", quote: x.quote ?? null, items: [] };
    g.items.push(x);
    out.set(key, g);
  }
  return [...out.values()];
}

const PAGE = 25;

/** A decision list in pages of 25: long lists never render in full. */
function List({ children }: { children: React.ReactNode }) {
  const [shown, setShown] = useState(PAGE);
  const items = Children.toArray(children);
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-border-soft overflow-hidden rounded-card bg-surface-muted">{items.slice(0, shown)}</ul>
      {items.length > shown ? (
        <Button size="sm" variant="ghost" onClick={() => setShown((n) => n + PAGE)}>
          Show {Math.min(PAGE, items.length - shown)} more
        </Button>
      ) : null}
    </div>
  );
}

type Kind = "all" | "listings" | "peers" | "pages" | "disclosures" | "exposures" | "facts";

export function AiDecisionsView() {
  const [days, setDays] = useState<number>(7);
  const [kind, setKind] = useState<Kind>("all");
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/approvals/ai-summary", { params: { query: { days } } }),
    [days],
  );
  const { state } = useApiResource(fetcher);
  const raw = dataOf(state);
  const d = raw ? parseAiDecisions(raw) : null;
  const api = getBrowserApiClient();

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="AI decisions"
        description="Everything the AI approvers decided for you: listings, peers, investor pages and facts found on the web. Nothing waits for a click; reject anything you disagree with and it stays rejected."
        aside={
          <div className="flex gap-1">
            {WINDOWS.map((w) => (
              <Button key={w} size="sm" variant={w === days ? "default" : "outline"} onClick={() => setDays(w)}>
                {w} days
              </Button>
            ))}
          </div>
        }
      />
      <ResourceNotice state={state} what="the AI decisions" />
      {d ? (
        <>
          <KpiStrip
            label="AI decisions at a glance"
            tiles={[
              {
                label: `Decisions in ${days} days`,
                value: totalDecisions(d),
                verdict: { tone: "neutral", text: "Nothing waited for a click. Reject any you disagree with." },
              },
              { label: "Listings and peers", value: d.listings.length + d.peers.length, verdict: { tone: "neutral", text: "Instrument listings and peer suggestions." } },
              { label: "Relationships and exposures", value: d.disclosures.length + d.exposures.length, verdict: { tone: "neutral", text: "Read from filings and annual reports." } },
              { label: "Web facts and pages", value: d.facts.length + d.pages.length, verdict: { tone: "neutral", text: "Found where the data sources had nothing." } },
            ]}
          />
          <FilterChips
            label="Kind of decision"
            value={kind}
            onChange={setKind}
            chips={[
              { key: "all", label: "All", count: totalDecisions(d) },
              { key: "listings", label: "Listings", count: d.listings.length },
              { key: "peers", label: "Peers", count: d.peers.length },
              { key: "pages", label: "Investor pages", count: d.pages.length },
              { key: "disclosures", label: "Customers and suppliers", count: d.disclosures.length },
              { key: "exposures", label: "Currencies and inputs", count: d.exposures.length },
              { key: "facts", label: "Web facts", count: d.facts.length },
            ]}
          />
        </>
      ) : null}
      {d && totalDecisions(d) === 0 ? <EmptyState title="No AI decisions in this window" /> : null}
      {d && d.listings.length > 0 && (kind === "all" || kind === "listings") ? (
        <Panel id="listings" title="Listings" description="Suggested instrument listings the AI confirmed or rejected.">
          <List>
            {d.listings.map((l) => (
              <Row
                key={l.ticker + l.decidedAt}
                ticker={l.ticker}
                undo={async () =>
                  !(await api.POST("/reference/identity/{ticker}/override", {
                    params: { path: { ticker: l.ticker } },
                    body: { decision: "rejected", reason: "rejected by the owner after an AI decision" },
                  })).error
                }
              >
                <span className="text-text-muted">
                  {l.status} · {formatDate(l.decidedAt)} · {l.reason}
                </span>
              </Row>
            ))}
          </List>
        </Panel>
      ) : null}
      {d && d.peers.length > 0 && (kind === "all" || kind === "peers") ? (
        <Panel id="peers" title="Peers" description="Peer suggestions the AI decided.">
          <List>
            {d.peers.map((p) => (
              <Row
                key={p.id}
                ticker={p.ticker}
                undo={async () =>
                  !(await api.PUT("/holdings/{ticker}/peer-set/{peer_id}/status", {
                    params: { path: { ticker: p.ticker, peer_id: p.id } },
                    body: { status: "rejected", reason: "rejected by the owner after an AI decision" },
                  })).error
                }
              >
                <span>{p.peer}</span>{" "}
                <span className="text-text-muted">
                  {p.status} · {formatDate(p.at)} · {p.reason}
                </span>
              </Row>
            ))}
          </List>
        </Panel>
      ) : null}
      {d && d.pages.length > 0 && (kind === "all" || kind === "pages") ? (
        <Panel
          id="pages"
          title="Investor pages"
          description="Pages added to a holding's profile. Change or clear them in the holding's research profile under Config."
        >
          <ul className="divide-y divide-border-soft overflow-hidden rounded-card bg-surface-muted">
            {d.pages.map((p) => (
              <li key={p.ticker + p.at} className="px-3 py-2 text-sm">
                <Link href={`/companies/${encodeURIComponent(p.ticker)}`} className="font-medium text-accent-ink hover:underline">
                  <EntityName id={p.ticker} chip={false} />
                </Link>{" "}
                <span className="text-text-muted">
                  {p.applied.join(", ")}, {formatDate(p.at)}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      {d && d.disclosures.length > 0 && (kind === "all" || kind === "disclosures") ? (
        <Panel id="disclosures" title="Customers and suppliers" description="Relationships the AI confirmed from filings. Rejecting one removes it from concentration.">
          <List>
            {d.disclosures.map((x) => (
              <Row
                key={x.id}
                ticker={x.ticker}
                undo={async () =>
                  !(await api.POST("/holdings/{ticker}/relationship-disclosures/{disclosure_id}/decision", {
                    params: { path: { ticker: x.ticker, disclosure_id: x.id } },
                    body: { decision: "reject", reason: "rejected by the owner after an AI decision" },
                  })).error
                }
              >
                <span>
                  {x.relationship}: <span className="font-medium">{x.counterparty}</span>
                </span>{" "}
                <span className="num">{x.share === null ? "" : `${(x.share * 100).toFixed(0)}% of revenue`}</span>
                <span className="block text-xs text-text-muted">
                  {[x.basis, periodWords(x.period), `decided ${formatDate(x.at)}`].filter(Boolean).join(", ")}. {x.reason}
                </span>
              </Row>
            ))}
          </List>
        </Panel>
      ) : null}
      {d && d.exposures.length > 0 && (kind === "all" || kind === "exposures") ? (
        <Panel id="exposures" title="Currencies and inputs" description="Exposures the AI added to holding profiles from annual reports, each with its quote. Rejecting one removes it for good.">
          <List>
            {groupExposures(d.exposures).map((g) => (
              <li key={g.key} className="space-y-1.5 px-3 py-2.5 text-sm">
                <p>
                  <Link href={`/companies/${encodeURIComponent(g.ticker)}`} className="font-medium text-accent-ink hover:underline">
                    <EntityName id={g.ticker} chip={false} />
                  </Link>{" "}
                  <span className="text-text-muted">
                    {g.kind === "commodity_input" ? (g.items.length === 1 ? "input" : "inputs") : g.kind.replaceAll("_", " ")}
                    {g.period ? `, ${periodWords(g.period)}` : ""}
                  </span>
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {g.items.map((x) => (
                    <ExposureChip
                      key={x.name}
                      x={x}
                      undo={async () =>
                        !(await api.POST("/holdings/{ticker}/exposures/remove", {
                          params: { path: { ticker: x.ticker } },
                          body: { kind: x.kind, name: x.name },
                        })).error
                      }
                    />
                  ))}
                </div>
                {g.quote ? <blockquote className="border-l-2 border-border pl-3 text-xs text-text-muted">&ldquo;{g.quote}&rdquo;</blockquote> : null}
              </li>
            ))}
          </List>
        </Panel>
      ) : null}
      {d && d.facts.length > 0 && (kind === "all" || kind === "facts") ? (
        <Panel id="facts" title="Facts from the web" description="Found on reliable web pages where the data sources had nothing.">
          <List>
            {d.facts.map((f) => (
              <Row
                key={f.id}
                ticker={f.ticker}
                undo={async () =>
                  !(await api.POST("/web-facts/{fact_id}/decision", {
                    params: { path: { fact_id: f.id } },
                    body: { status: "rejected" },
                  })).error
                }
              >
                <span>{SLOT_LABELS[f.slot] ?? f.slot}</span> <span className="num">{displayValue(f)}</span>{" "}
                <span className="text-text-muted">
                  {f.tier} · {f.status}
                  {f.confidence !== null ? ` (${f.confidence.toFixed(2)})` : ""} · {formatDate(f.retrievedAt)}
                </span>
              </Row>
            ))}
          </List>
        </Panel>
      ) : null}
    </div>
  );
}
