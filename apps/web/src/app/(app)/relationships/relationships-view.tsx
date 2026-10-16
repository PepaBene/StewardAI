"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { dataOf, ResourceNotice, SectionNav } from "@/components/oversight/panel";
import { Field } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { normTicker, parseNetwork, parseQueue, parseReconciliations, validateTicker, type Disclosure } from "@/lib/relationships/relationships";

import { ActionsPanel } from "./actions-panel";
import { AllRecordsPanel } from "./all-records-panel";
import { ContradictionsPanel } from "./contradictions-panel";
import { EventsPanel } from "./events-panel";
import { GraphPanel } from "./graph-panel";
import { SuggestionsPanel } from "./suggestions-panel";

const SECTIONS = [
  { id: "suggested", label: "Suggested relationships" },
  { id: "contradictions", label: "Contradictions" },
  { id: "graph", label: "Graph edges" },
  { id: "decisions", label: "Disclosure and event decisions" },
  { id: "actions", label: "Corporate actions" },
  { id: "all-records", label: "All recorded relationships" },
] as const;

const fetchQueue = () => getBrowserApiClient().GET("/relationship-review/queue", { params: { query: { limit: 500 } } });
const fetchReconciliations = () => getBrowserApiClient().GET("/company-graph/reconciliations");
const NONE = async (): Promise<ApiResult<unknown>> => ({ data: {}, response: new Response(null, { status: 200 }) });

/**
 * Relationship review: every graph fact that waits for an owner decision. Nothing here merges
 * or confirms by itself; each row says what is known, links its source, and takes a decision
 * with a required reason.
 */
export function RelationshipsView() {
  const initial = useSearchParams().get("ticker") ?? "";
  const [input, setInput] = useState(initial && !validateTicker(initial) ? initial : "");
  const [ticker, setTicker] = useState<string | null>(initial && !validateTicker(initial) ? normTicker(initial) : null);
  const [error, setError] = useState<string | undefined>();

  const queue = useApiResource(fetchQueue);
  const recon = useApiResource(fetchReconciliations);
  const fetchNetwork = useCallback(() => (ticker ? getBrowserApiClient().GET("/holdings/{ticker}/business-network", { params: { path: { ticker } } }) : NONE()), [ticker]);
  const network = useApiResource<unknown>(fetchNetwork);

  const qData = dataOf(queue.state);
  const q = useMemo(() => (qData ? parseQueue(qData) : null), [qData]);
  const nData = ticker ? dataOf(network.state) : null;
  const net = useMemo(() => (nData && ticker ? parseNetwork(nData, ticker) : null), [nData, ticker]);
  const rData = dataOf(recon.state);
  const reconAll = useMemo(() => (rData ? parseReconciliations(rData) : []), [rData]);

  // A decided row left the open queue; the holding's own list says what it is now.
  const byId = useMemo(() => {
    const m = new Map<number, Disclosure>();
    for (const d of net?.disclosures ?? []) m.set(d.id, d);
    for (const d of q?.disclosures ?? []) m.set(d.id, { ...d, status: m.get(d.id)?.status ?? d.status });
    return m;
  }, [q, net]);

  const forTicker = <T extends { ticker: string | null }>(rows: T[]) => (ticker ? rows.filter((r) => r.ticker === ticker) : rows);
  const queueDisclosures = (q?.disclosures ?? []).map((d) => ({ ...d, status: byId.get(d.id)?.status ?? d.status }));
  const reloadAll = () => {
    queue.reload();
    recon.reload();
    network.reload();
  };

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const t = normTicker(input);
    if (t === "") {
      setError(undefined);
      setTicker(null);
      return;
    }
    const err = validateTicker(t);
    setError(err);
    if (!err) setTicker(t);
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Relationship review"
        description="Customer and supplier links, source conflicts, graph edges and corporate actions that wait for your decision. Nothing is confirmed or merged until you decide, and every decision keeps your reason."
      />
      <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3 border-y border-border-soft py-3" aria-label="Choose a holding">
        <Field label="Holding" hint="Narrows the lists to one ticker. Graph edges, decisions and corporate actions need one." error={error}>
          <Input value={input} onChange={(e) => setInput(e.target.value)} maxLength={32} className="num w-56" placeholder="e.g. AAPL_US_EQ" />
        </Field>
        <Button type="submit" size="sm">
          Show holding
        </Button>
        {ticker ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setInput("");
              setTicker(null);
              setError(undefined);
            }}
          >
            Show all holdings
          </Button>
        ) : null}
      </form>
      <SectionNav sections={SECTIONS} />
      <ResourceNotice state={queue.state} what="the review queue" />
      <SuggestionsPanel hits={forTicker(q?.hits ?? [])} disclosures={forTicker(queueDisclosures)} onChanged={reloadAll} />
      <ContradictionsPanel items={forTicker(q?.contradictions ?? [])} byId={byId} onChanged={reloadAll} />
      <GraphPanel
        ticker={ticker}
        edges={net?.edges ?? null}
        edgesNote={ticker ? <ResourceNotice state={network.state} what={`the graph of ${ticker}`} /> : <ResourceNotice state={recon.state} what="parent and successor changes" />}
        reconciliations={forTicker(reconAll)}
        onChanged={reloadAll}
      />
      <EventsPanel ticker={ticker} disclosures={net?.disclosures ?? null} events={net?.events ?? null} note={ticker ? <ResourceNotice state={network.state} what={`records for ${ticker}`} /> : null} onChanged={reloadAll} />
      <ActionsPanel ticker={ticker} />
      <AllRecordsPanel ticker={ticker} />
      <p className="text-xs text-text-muted">
        Related:{" "}
        <Link href="/review" className="text-accent-ink hover:underline">
          Review queue
        </Link>{" "}
        ·{" "}
        <Link href="/verification" className="text-accent-ink hover:underline">
          Verification
        </Link>{" "}
        ·{" "}
        <Link href="/holdings" className="text-accent-ink hover:underline">
          Holdings
        </Link>
      </p>
    </div>
  );
}
