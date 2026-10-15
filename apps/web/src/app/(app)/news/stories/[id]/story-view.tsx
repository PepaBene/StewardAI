"use client";

import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsFeedback } from "@/components/news/news-feedback";
import { NewsTabs } from "@/components/news/news-tabs";
import { TierBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { useTrackOpen } from "@/lib/use-track-open";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type StoryEvidence } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import {
  claimTotals,
  EVENT_STATUS_LABELS,
  exposurePct,
  LIFECYCLE_LABELS,
  NOVELTY_LABELS,
  RELATION_LABELS,
  SENTIMENT_LABELS,
  signedPct,
  SURPRISE_LABELS,
  THESIS_STATUS_LABELS,
  VERDICT_LABELS,
  type StoryAnalysis,
} from "@/lib/news/analysis";
import { eventLabel } from "@/lib/news/labels";
import { isExternalUrl } from "@/lib/sources/sources";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; evidence: StoryEvidence; analysis: StoryAnalysis | null; analysisError: string | null };

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="blueprint space-y-3 px-5 py-4">
      <BlueprintCorners />
      <div>
        <h2 className="text-[20px] leading-tight font-semibold">{title}</h2>
        {note ? <p className="text-xs text-text-muted">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** One story: why it is linked and confirmed, then everything the analysis engine found in it. */
export function StoryView({ id }: { id: number }) {
  useTrackOpen("news_story", id);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);
  // Only the newest request may write: a late answer for an older story or refresh is dropped.
  const requestSeq = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current;
    const api = getBrowserApiClient();
    try {
      const [evidence, analysis] = await Promise.all([
        api.GET("/stories/{story_id}/evidence", { params: { path: { story_id: id } } }),
        // A failed analysis request is its own error, not "not analysed yet".
        api.GET("/stories/{story_id}/analysis", { params: { path: { story_id: id } } }).catch((err: unknown) => ({
          data: undefined,
          response: undefined,
          failure: err instanceof Error ? err.message : String(err),
        })),
      ]);
      if (seq !== requestSeq.current) return;
      if (evidence.data) {
        const analysisData = analysis.data as StoryAnalysis | undefined;
        const failure = "failure" in analysis ? analysis.failure : analysis.response ? `The API answered HTTP ${analysis.response.status}.` : "No answer.";
        setLoad({ kind: "ok", evidence: evidence.data, analysis: analysisData ?? null, analysisError: analysisData ? null : failure });
      } else
        setLoad({
          kind: "error",
          message: evidence.response.status === 404 ? "This story does not exist." : `The API answered HTTP ${evidence.response.status}.`,
        });
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  }, [id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    void refresh();
  }, [refresh]);

  async function toggleClosed(closed: boolean) {
    setClosing(true);
    setCloseError(null);
    try {
      const { response } = await getBrowserApiClient().POST("/stories/{story_id}/close", {
        params: { path: { story_id: id } },
        body: { closed },
      });
      if (!response.ok) setCloseError(`The API answered HTTP ${response.status}; the story was not ${closed ? "closed" : "reopened"}.`);
      else await refresh();
    } catch (err) {
      setCloseError(err instanceof Error ? err.message : String(err));
    } finally {
      setClosing(false);
    }
  }

  if (load.kind === "loading")
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading the story…
      </p>
    );
  if (load.kind === "error") return <AlertMessage title="Could not load the story">{load.message}</AlertMessage>;

  const { evidence, analysis, analysisError } = load;
  const hasAnalysis = analysis !== null && Object.entries(analysis).some(([k, v]) => k !== "story_id" && k !== "models" && v !== null);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker={
          <>
            <Link href="/news/stories" className="hover:underline">
              Stories
            </Link>{" "}
            / {id}
          </>
        }
        title={evidence.headline}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-text">{LIFECYCLE_LABELS[evidence.lifecycle] ?? evidence.lifecycle}</span>
            {evidence.lifecycle_reason ? <span>{evidence.lifecycle_reason}</span> : null}
          </span>
        }
        aside={
          <>
            <NewsFeedback target="story" targetId={id} label={`story “${evidence.headline}”`} />
            <Button variant="outline" size="sm" disabled={closing} onClick={() => void toggleClosed(evidence.closed_at === null)}>
              {evidence.closed_at === null ? "Close the story" : "Reopen"}
            </Button>
          </>
        }
      />
      <NewsTabs active="stories" />
      {closeError ? <AlertMessage title="Could not change the story">{closeError}</AlertMessage> : null}

      <Panel
        title="Evidence"
        note={`${evidence.independent_sources} independent source${evidence.independent_sources === 1 ? "" : "s"}, ${evidence.primary_evidence} primary. Copies of one wire story count once.`}
      >
        <ul className="m-0 list-none divide-y divide-border p-0">
          {evidence.items.map((item) => (
            <li key={item.id} id={`item-${item.id}`} className="space-y-1 py-2 text-sm">
              <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                <TierBadge tier={item.tier} />
                <span>{item.outlet ?? "Unknown outlet"}</span>
                {item.kind ? <span>{item.kind.replace("_", " ")}</span> : <span>kind not recorded</span>}
                {item.is_correction ? <span className="font-medium text-text">Correction notice</span> : null}
                <time dateTime={item.published_at} className="num">
                  {formatDateTime(item.published_at)}
                </time>
              </p>
              {isExternalUrl(item.url) ? (
                <a href={item.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent-ink hover:underline">
                  <span className="[overflow-wrap:anywhere]">{item.headline}</span>
                  <ExternalLinkIcon aria-hidden="true" className="mt-1 size-3 shrink-0 text-text-muted" />
                </a>
              ) : (
                <span className="inline-flex [overflow-wrap:anywhere]">{item.headline}</span>
              )}
              {item.links.length > 0 ? (
                <p className="text-xs text-text-muted">
                  Linked to {item.links.map((l) => `${l.ticker} by ${l.via}`).join(", ")}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
        {evidence.history.length > 0 ? (
          <ol className="m-0 list-none space-y-1 p-0 text-sm" aria-label="Status history">
            {evidence.history.map((h) => (
              <li key={h.at + h.to_state}>
                <time dateTime={h.at} className="num text-text-muted">
                  {formatDateTime(h.at)}
                </time>{" "}
                {h.from_state ? `${LIFECYCLE_LABELS[h.from_state] ?? h.from_state} to ` : ""}
                <span className="font-medium">{LIFECYCLE_LABELS[h.to_state] ?? h.to_state}</span>: {h.reason}
              </li>
            ))}
          </ol>
        ) : null}
      </Panel>

      {analysisError ? <AlertMessage title="Could not load the analysis">{analysisError}</AlertMessage> : null}
      {!hasAnalysis && !analysisError ? (
        <EmptyState title="Not analysed yet">
          The news analysis job writes novelty, the story thread and the other findings here. Turn on more techniques under
          Config, Jobs, News.
        </EmptyState>
      ) : null}
      {analysis ? <AnalysisPanels analysis={analysis} items={evidence.items} /> : null}
    </div>
  );
}

/** Which stored item a quoted span comes from: the outlet, linked to that item in the Evidence list. */
function ItemRef({ id, items }: { id: number; items: StoryEvidence["items"] }) {
  const item = items.find((i) => i.id === id);
  if (!item) return <span>(item {id})</span>;
  return (
    <a href={`#item-${id}`} className="hover:text-accent-ink hover:underline">
      ({item.outlet ?? `item ${id}`})
    </a>
  );
}

function AnalysisPanels({ analysis, items }: { analysis: StoryAnalysis; items: StoryEvidence["items"] }) {
  const { extraction, novelty, impact, thread, framing, claims, market, thesis, sentiment } = analysis;
  return (
    <>
      {extraction ? (
        <Panel title="The event" note="Facts quote the stored text; what outlets argue and what might happen are kept apart.">
          <p className="text-sm">
            <span className="font-medium">{eventLabel(extraction.event_type as never)}</span>: {extraction.action}.{" "}
            {EVENT_STATUS_LABELS[extraction.status]}.
            {extraction.magnitude ? ` ${extraction.magnitude.text}.` : ""}
          </p>
          <ul className="m-0 list-none space-y-2 p-0">
            {extraction.key_claims.map((c, i) => (
              <li key={i} className="space-y-0.5 text-sm">
                <p className="flex flex-wrap items-center gap-2">
                  <span>{c.text}</span>
                  <NewsFeedback target="claim" targetId={`${analysis.story_id}:${i}`} label={`claim “${c.text}”`} />
                </p>
                <blockquote className="border-l-2 border-border pl-3 text-xs text-text-muted [overflow-wrap:anywhere]">
                  &ldquo;{c.span}&rdquo; <ItemRef id={c.item_id} items={items} />
                </blockquote>
              </li>
            ))}
          </ul>
          <TextList title="Interpretation by the outlets" items={extraction.interpretation} />
          <TextList title="Speculation" items={extraction.speculation} />
          <TextList title="Still unknown" items={extraction.unknowns} />
          {extraction.affected_entities.length > 0 ? (
            <p className="text-xs text-text-muted">
              Affects {extraction.affected_entities.map((e) => `${e.name} (${e.kind}, ${e.direction})`).join(", ")}.
            </p>
          ) : null}
        </Panel>
      ) : null}

      {novelty ? (
        <Panel title="Is it new?">
          <p className="text-sm">
            <span className="font-medium">{NOVELTY_LABELS[novelty.novelty]}.</span> {SURPRISE_LABELS[novelty.surprise]}.{" "}
            <span className="text-text-muted">
              {novelty.similarity !== null && novelty.similarity !== undefined
                ? `Closest earlier story ${novelty.similar_story_id ?? ""} at ${(novelty.similarity * 100).toFixed(0)}% similarity. `
                : ""}
              {novelty.basis}.
            </span>
            {novelty.similar_story_id ? (
              <>
                {" "}
                <Link href={`/news/stories/${novelty.similar_story_id}`} className="text-accent-ink hover:underline">
                  Open it
                </Link>
              </>
            ) : null}
          </p>
        </Panel>
      ) : null}

      {impact ? (
        <Panel
          title="Reach into your portfolio"
          note="Exposure is the share of the portfolio in the companies reached, not a forecast of return. Links come from the supply-chain graph; a possible link the graph does not hold is labelled."
        >
          {impact.chains.length === 0 ? <p className="text-sm text-text-muted">{impact.notes.join(" ") || "No holding is reached."}</p> : null}
          <ul className="m-0 list-none space-y-2 p-0">
            {impact.chains.map((c, i) => (
              <li key={i} className="space-y-0.5 border-l-2 border-border pl-3 text-sm">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{c.holding}</span>
                  <span className="text-text-muted">{RELATION_LABELS[c.relation]}</span>
                  <span className="num">{c.weight === null || c.weight === undefined ? "no weight" : exposurePct(c.weight)}</span>
                  <span className="text-xs text-text-muted">{c.confidence} confidence</span>
                </p>
                {c.chain.length > 1 ? <p className="font-mono text-xs text-text-muted">{c.chain.join(" > ")}</p> : null}
                {c.links.map((l, j) => (
                  <p key={`${j}:${l}`} className="text-xs text-text-muted">
                    {l}
                  </p>
                ))}
                {c.note ? <p className="text-sm">{c.note}</p> : null}
              </li>
            ))}
          </ul>
          <p className="num text-xs text-text-muted">
            Held directly {exposurePct(impact.direct_weight)}, reached through links {exposurePct(impact.reached_weight)}, shared
            sector, country or currency {exposurePct(impact.attribute_weight)}.
          </p>
        </Panel>
      ) : null}

      {sentiment && sentiment.aspects.length > 0 ? (
        <Panel title="Direction of the text" note="Read from the article text, not a forecast of the price. Each reading quotes its sentence.">
          <ul className="m-0 list-none space-y-2 p-0">
            {sentiment.aspects.map((a, i) => (
              <li key={i} className="space-y-0.5 text-sm">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{a.entity}</span>
                  <span>{SENTIMENT_LABELS[a.direction]}</span>
                  <span className="num text-xs text-text-muted">strength {a.strength.toFixed(2)}</span>
                </p>
                <blockquote className="border-l-2 border-border pl-3 text-xs text-text-muted [overflow-wrap:anywhere]">
                  &ldquo;{a.span}&rdquo; <ItemRef id={a.item_id} items={items} />
                </blockquote>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {thread ? (
        <Panel title="What changed in this situation">
          <p className="text-sm">
            {thread.opened ? "This story opened a new thread." : "This story belongs to an earlier thread."}{" "}
            <Link href="/news/stories" className="text-accent-ink hover:underline">
              See all threads
            </Link>
          </p>
          <TextList title="New" items={thread.diff.new} />
          {thread.diff.changed.length > 0 ? (
            <div>
              <p className="kicker">Changed</p>
              <ul className="m-0 list-none space-y-1 p-0 text-sm">
                {thread.diff.changed.map((c, i) => (
                  <li key={`${i}:${c.now}`}>
                    {c.now} <span className="text-text-muted">(was: {c.was})</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <TextList title="Corrected" items={thread.diff.retracted} />
        </Panel>
      ) : null}

      {framing ? (
        <Panel title="How outlets frame it" note="Differences are shown, not scored. Outlet region and state ownership are context.">
          <p className="text-sm font-medium">
            {framing.agreement === "agree" ? "The outlets agree on the facts." : framing.agreement === "conflict" ? "The outlets conflict on a fact." : "The facts agree; the framing differs by region."}
          </p>
          <TextList title="Conflicts" items={framing.conflicts} />
          {framing.divergences.map((d, i) => (
            <p key={`${i}:${d.summary}`} className="text-sm">
              <span className="font-mono text-xs">{d.regions.join(", ")}</span> {d.summary}
            </p>
          ))}
          <ul className="m-0 list-none space-y-1 p-0 text-sm">
            {framing.outlets.map((o) => (
              <li key={o.item_id}>
                <span className="text-text-muted"><ItemRef id={o.item_id} items={items} />:</span> {o.headline_claim}
                {o.emphasised_cause ? <span className="text-text-muted"> (emphasises {o.emphasised_cause})</span> : null}
                {o.omitted.length > 0 ? <span className="text-text-muted"> (leaves out {o.omitted.join("; ")})</span> : null}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {claims ? <ClaimsPanel claims={claims.claims} /> : null}

      {market ? (
        <Panel
          title="What the market did"
          note="A description of the past, measured from the last close before the story. Not a prediction."
        >
          <table aria-label="Price move after publication" className="num w-full max-w-md text-sm">
            <thead className="text-left text-xs text-text-muted">
              <tr>
                <th scope="col" className="py-1 font-normal">After</th>
                <th scope="col" className="py-1 font-normal">Holding</th>
                <th scope="col" className="py-1 font-normal">{market.benchmark ?? "Benchmark"}</th>
                <th scope="col" className="py-1 font-normal">Difference</th>
              </tr>
            </thead>
            <tbody>
              {market.windows.map((w) => (
                <tr key={w.days}>
                  <td className="py-0.5">{w.days} trading day{w.days === 1 ? "" : "s"}</td>
                  <td>{signedPct(w.stock_pct)}</td>
                  <td>{signedPct(w.benchmark_pct)}</td>
                  <td>{signedPct(w.abnormal_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {market.notes.map((n, i) => (
            <p key={`${i}:${n}`} className="text-xs text-text-muted">
              {n}.
            </p>
          ))}
          {!market.complete ? <p className="text-xs text-text-muted">Later windows fill in as prices arrive.</p> : null}
        </Panel>
      ) : null}

      {thesis ? (
        <Panel title="Against your thesis" note="Only a story confirmed by a primary source can mark a breach; any other weakening is a prompt to review.">
          {thesis.links.length === 0 ? <p className="text-sm text-text-muted">The story touches no assumption of the thesis.</p> : null}
          <ul className="m-0 list-none space-y-2 p-0 text-sm">
            {thesis.links.map((l) => (
              <li key={l.kind + l.ref}>
                <span className="font-medium">{THESIS_STATUS_LABELS[l.status]}.</span> {l.kind === "why_owned" ? "Why you own it" : l.ref}: {l.reason}
                <span className="block text-xs text-text-muted">Because: {l.claim}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </>
  );
}

function ClaimsPanel({ claims }: { claims: NonNullable<StoryAnalysis["claims"]>["claims"] }) {
  const totals = claimTotals(claims);
  return (
    <Panel
      title="Claim check against primary documents"
      note={`${totals.supported} supported, ${totals.contradicted} contradicted, ${totals.unverified} not verified.`}
    >
      <ul className="m-0 list-none space-y-2 p-0 text-sm">
        {claims.map((c, i) => (
          <li key={`${i}:${c.claim}`} className="space-y-0.5">
            <p>
              <span className="font-medium">{VERDICT_LABELS[c.verdict]}.</span> {c.claim}
            </p>
            {c.quote && c.primary_url ? (
              <blockquote className="border-l-2 border-border pl-3 text-xs text-text-muted [overflow-wrap:anywhere]">
                &ldquo;{c.quote}&rdquo;{" "}
                {isExternalUrl(c.primary_url) ? (
                  <a href={c.primary_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                    {c.primary_outlet ?? "primary document"}
                  </a>
                ) : (
                  <span>{c.primary_outlet ?? "primary document"}</span>
                )}
              </blockquote>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function TextList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="kicker">{title}</p>
      <ul className="m-0 list-disc space-y-0.5 pl-5 text-sm">
        {items.map((t, i) => (
          <li key={`${i}:${t}`}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

