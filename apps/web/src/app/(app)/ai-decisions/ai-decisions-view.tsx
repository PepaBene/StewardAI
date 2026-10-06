"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseAiDecisions, totalDecisions } from "@/lib/reference/ai-decisions";
import { SLOT_LABELS, displayValue } from "@/lib/reference/web-facts";

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
        <Link href={`/holdings/${encodeURIComponent(ticker)}`} className="num font-medium text-accent-ink hover:underline">
          {ticker}
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

function List({ children }: { children: React.ReactNode }) {
  return <ul className="divide-y divide-border border border-border">{children}</ul>;
}

export function AiDecisionsView() {
  const [days, setDays] = useState<number>(7);
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
        kicker="Oversight"
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
      {d && totalDecisions(d) === 0 ? <EmptyState title="No AI decisions in this window" /> : null}
      {d && d.listings.length > 0 ? (
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
      {d && d.peers.length > 0 ? (
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
      {d && d.pages.length > 0 ? (
        <Panel
          id="pages"
          title="Investor pages"
          description="Pages added to a holding's profile. Change or clear them in the holding's research profile under Config."
        >
          <ul className="divide-y divide-border border border-border">
            {d.pages.map((p) => (
              <li key={p.ticker + p.at} className="px-3 py-2 text-sm">
                <Link href={`/holdings/${encodeURIComponent(p.ticker)}`} className="num font-medium text-accent-ink hover:underline">
                  {p.ticker}
                </Link>{" "}
                <span className="text-text-muted">
                  {formatDate(p.at)} · {p.applied.join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
      {d && d.facts.length > 0 ? (
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
