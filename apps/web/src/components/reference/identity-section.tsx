"use client";

import {
  BanIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  HouseIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  ScaleIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type HoldingIdentity,
  type LegalEntityOut,
  type LiquidityOut,
  type ListingOut,
  type RelationshipDecision,
  type RelationshipOut,
} from "@/lib/api/client";
import { historyHref } from "@/lib/config/domains";
import { formatDate, formatDateTime, formatMoney, formatQuantity, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  CONSOLIDATION_NOTE,
  decisionMessage,
  describeException,
  disputeChoices,
  gleifHref,
  groupRelated,
  listingState,
  parseConflict,
  parseWikidata,
  RELATIONSHIP_LABELS,
  safeHref,
  sortListings,
  sortParents,
  sourceLabel,
  tierLabel,
  wikidataHref,
  type WikidataSummary,
} from "@/lib/reference/identity";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;
const POLL_LIMIT_MS = 3 * 60 * 1000;
const LINK = "text-accent underline-offset-4 hover:underline";

type Decision = { action: "confirm" | "reject"; result: RelationshipDecision };

/**
 * Holding page, identity and structure (Phase 2b): identifiers, listings, liquidity, the
 * legal entity, GLEIF group structure, and relationships waiting for the owner's decision.
 */
export function useHoldingIdentity(ticker: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/identity", { params: { path: { ticker } } }),
    [ticker],
  );
  return useApiResource(fetcher);
}

export function IdentitySection({ ticker, resource }: { ticker: string; resource: ReturnType<typeof useHoldingIdentity> }) {
  const { state, reload } = resource;
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const notFound = state.kind === "error" && state.status === 404;

  // After "Refresh reference data", re-read until the resolution time changes.
  const [refresh, setRefresh] = useState<{ jobId: number; since: number; before: string | null } | null>(null);
  const resolvedAt = data?.identity_resolved_at ?? null;
  const refreshing = refresh !== null && resolvedAt === refresh.before;
  useEffect(() => {
    if (!refreshing || !refresh) return;
    const timer = setInterval(() => {
      if (Date.now() - refresh.since > POLL_LIMIT_MS) setRefresh(null);
      else reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [refreshing, refresh, reload]);

  return (
    <Blueprint aria-labelledby="identity-heading" className="space-y-3 px-[22px] py-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <PanelHeading kicker="Identity & structure" title={<span id="identity-heading">Identifiers, listings and group</span>} />
          {resolvedAt ? (
            <p className="flex items-center gap-1.5 text-xs text-text-muted">
              <ClockIcon aria-hidden="true" className="size-3.5" />
              <span className="num">
                Resolved <time dateTime={resolvedAt}>{formatDateTime(resolvedAt)}</time> ({formatRelative(new Date(resolvedAt))})
              </span>
            </p>
          ) : null}
        </div>
        <RefreshButton
          ticker={ticker}
          busy={refreshing}
          onQueued={(jobId) => setRefresh({ jobId, since: Date.now(), before: resolvedAt })}
        />
      </div>
      <div role="status" aria-live="polite" className="text-xs text-text-muted">
        {refresh && refreshing ? `Reference data job #${refresh.jobId} is running; this section updates when it finishes.` : null}
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading identity…
        </p>
      ) : null}
      {notFound && !data ? (
        <EmptyState title="No reference data yet">
          {ticker} has not been resolved through OpenFIGI, ESMA and GLEIF. Use <strong>Refresh reference data</strong>{" "}
          above, or wait for the weekly reference-data job.
        </EmptyState>
      ) : null}
      {state.kind === "error" && !notFound ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}
      {data ? <IdentityContent identity={data} reload={reload} /> : null}
    </Blueprint>
  );
}

function RefreshButton({ ticker, busy, onQueued }: { ticker: string; busy: boolean; onQueued: (jobId: number) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setPending(true);
    setError(null);
    try {
      const r = await getBrowserApiClient().POST("/jobs/reference-data", { body: { ticker, force: true } });
      if (r.data) onQueued(r.data.id);
      else setError(`Not queued (HTTP ${r.response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="outline" size="sm" onClick={() => void run()} disabled={pending || busy}>
        {pending || busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
        Refresh reference data
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function IdentityContent({ identity, reload }: { identity: HoldingIdentity; reload: () => void }) {
  const wikidata = parseWikidata(identity.wikidata);
  return (
    <div className="space-y-4">
      {identity.identity_error ? (
        <AlertMessage title="Identity incomplete">
          <p>{identity.identity_error}</p>
          <p className="mt-1">
            Until it is resolved, news matching and price history for this holding may be incomplete. See every{" "}
            <Link href="/status#reference-gaps" className={LINK}>
              reference data gap
            </Link>
            .
          </p>
        </AlertMessage>
      ) : null}

      <DisputedRelationships identity={identity} reload={reload} />
      <Suggestions identity={identity} reload={reload} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Identifiers">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <Row label="ISIN" mono value={identity.isin} />
            <Row label="Share-class FIGI" mono value={identity.share_class_figi} />
            <Row label="Security type" value={identity.security_type} />
            <Row
              label="Home market"
              value={
                identity.home_mic ? (
                  <>
                    <span className="font-mono">{identity.home_mic}</span>
                    {identity.home_country ? <span className="text-text-muted"> · {identity.home_country}</span> : null}
                  </>
                ) : null
              }
            />
            <Row label="Stooq symbol" mono value={identity.price_symbol} />
          </dl>
        </Panel>
        <LegalEntityPanel entity={identity.legal_entity} />
      </div>

      <CompositeListings identity={identity} />
      <VenueListings listings={identity.listings} />
      {identity.liquidity ? <LiquidityPanel liquidity={identity.liquidity} /> : null}
      <GroupStructure identity={identity} />
      {wikidata ? <WikidataPanel summary={wikidata} /> : null}
      {identity.aliases.length > 0 ? (
        <details className="rounded-lg border border-border bg-surface-raised p-3 text-sm">
          <summary className="cursor-pointer font-medium">
            News aliases <span className="num text-text-muted">({identity.aliases.length})</span>
          </summary>
          <p className="mt-1 text-xs text-text-muted">Names the news pipeline matches to this holding.</p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {identity.aliases.map((a) => (
              <li
                key={`${a.kind}:${a.alias}`}
                className="rounded-sm border border-border bg-surface-muted px-1.5 py-0.5 text-xs"
                title={`${a.kind} · ${sourceLabel(a.source)}`}
              >
                {a.alias} <span className="text-text-muted">· {sourceLabel(a.source)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Panel({ title, note, children, className }: { title: string; note?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-2 rounded-lg border border-border bg-surface-raised p-4", className)}>
      <div>
        <h3 className="font-medium">{title}</h3>
        {note ? <p className="text-xs text-text-muted">{note}</p> : null}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd className={cn("min-w-0 break-words", mono && "font-mono")}>
        {value === null || value === undefined || value === "" ? <span className="text-text-muted">Unknown</span> : value}
      </dd>
    </>
  );
}

function LeiLink({ lei }: { lei: string }) {
  return (
    <a href={gleifHref(lei)} target="_blank" rel="noreferrer" className={cn("inline-flex items-center gap-1 font-mono", LINK)}>
      {lei}
      <ExternalLinkIcon aria-hidden="true" className="size-3" />
      <span className="sr-only">(GLEIF record, opens in a new tab)</span>
    </a>
  );
}

function LegalEntityPanel({ entity }: { entity: LegalEntityOut | null }) {
  if (!entity) {
    return (
      <Panel title="Legal entity">
        <p className="text-sm text-text-muted">No issuer LEI found (common for funds and some foreign listings).</p>
      </Panel>
    );
  }
  return (
    <Panel
      title="Legal entity"
      note={
        <span className="num">
          GLEIF Level 1 · fetched <time dateTime={entity.fetched_at}>{formatDateTime(entity.fetched_at)}</time>
        </span>
      }
    >
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        <Row label="Legal name" value={entity.legal_name} />
        <Row label="LEI" value={<LeiLink lei={entity.lei} />} />
        <Row label="Jurisdiction" value={entity.jurisdiction} />
        <Row label="Legal form" value={entity.legal_form} />
        <Row label="Headquarters" value={entity.hq_country} />
        <Row label="Entity status" value={entity.status} />
        <Row label="Registration" value={entity.registration_status} />
        <Row label="Next renewal" value={entity.next_renewal ? <span className="num">{formatDate(new Date(entity.next_renewal))}</span> : null} />
        {entity.successor_lei ? (
          <Row
            label="Successor LEI"
            value={
              <span className="inline-flex flex-wrap items-center gap-1">
                <LeiLink lei={entity.successor_lei} />
                <span className="text-xs text-text-muted">(merged or replaced)</span>
              </span>
            }
          />
        ) : null}
        {entity.other_names.length > 0 ? <Row label="Other names" value={entity.other_names.join(" · ")} /> : null}
      </dl>
    </Panel>
  );
}

function CompositeListings({ identity }: { identity: HoldingIdentity }) {
  const rows = identity.identifiers;
  return (
    <Panel
      title="Listings (OpenFIGI)"
      note={
        <span className="num">
          {rows.length} composite (per-country) listing{rows.length === 1 ? "" : "s"} of {identity.identifier_count} OpenFIGI
          listing{identity.identifier_count === 1 ? "" : "s"} of the share class. Every listing ticker is a news alias.
        </span>
      }
    >
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">No OpenFIGI listings resolved.</p>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="py-1 pr-3 text-left font-medium">Ticker</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">Exchange code</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">Composite FIGI</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">FIGI</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">Type</th>
                <th scope="col" className="py-1 text-left font-medium">Name</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.figi} className="border-b border-border last:border-0">
                  <th scope="row" className="py-1 pr-3 text-left font-mono font-normal">{r.listing_ticker ?? "—"}</th>
                  <td className="py-1 pr-3 font-mono">{r.exch_code ?? "—"}</td>
                  <td className="py-1 pr-3 font-mono">{r.composite_figi ?? "—"}</td>
                  <td className="py-1 pr-3 font-mono">{r.figi}</td>
                  <td className="py-1 pr-3">{r.security_type ?? "—"}</td>
                  <td className="py-1">{r.name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ListingBadge({ listing }: { listing: ListingOut }) {
  const state = listingState(listing);
  if (state === "home") {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm border border-info-signal/40 bg-info-signal-subtle px-1.5 py-0.5 text-xs font-medium">
        <HouseIcon aria-hidden="true" className="size-3.5 text-info-signal" />
        Home market
      </span>
    );
  }
  if (state === "terminated") {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-xs font-medium text-text-muted">
        <BanIcon aria-hidden="true" className="size-3.5" />
        Terminated{listing.terminated_at ? <span className="num"> {formatDate(new Date(listing.terminated_at))}</span> : null}
      </span>
    );
  }
  return <span className="text-xs text-text-muted">Active</span>;
}

function VenueListings({ listings }: { listings: ListingOut[] }) {
  const rows = sortListings(listings);
  return (
    <Panel title="Trading venues (ESMA FIRDS)" note="EU venues admitting the ISIN. Tier 1 (regulator).">
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">No FIRDS records (not admitted to an EU venue, or not yet resolved).</p>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="py-1 pr-3 text-left font-medium">MIC</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">Status</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">Currency</th>
                <th scope="col" className="py-1 pr-3 text-left font-medium">CFI</th>
                <th scope="col" className="py-1 pr-3 text-right font-medium">First trade</th>
                <th scope="col" className="py-1 text-left font-medium">Instrument name</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((l) => {
                const terminated = listingState(l) === "terminated";
                return (
                  <tr
                    key={`${l.mic}:${l.first_trade_at ?? ""}`}
                    className={cn("border-b border-border last:border-0", terminated && "text-text-muted")}
                  >
                    <th scope="row" className="py-1 pr-3 text-left font-mono font-normal">{l.mic}</th>
                    <td className="py-1 pr-3">
                      <ListingBadge listing={l} />
                    </td>
                    <td className="py-1 pr-3">{l.currency ?? "—"}</td>
                    <td className="py-1 pr-3 font-mono">{l.cfi ?? "—"}</td>
                    <td className="num py-1 pr-3 text-right">{l.first_trade_at ? formatDate(new Date(l.first_trade_at)) : "—"}</td>
                    <td className="py-1">{l.full_name ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function LiquidityPanel({ liquidity }: { liquidity: LiquidityOut }) {
  return (
    <Panel
      title="Liquidity (ESMA FITRS)"
      note={liquidity.period_to ? <span className="num">Calculation period to {formatDate(liquidity.period_to)}</span> : undefined}
    >
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr_max-content_1fr]">
        <Row label="Liquid market" value={liquidity.liquid === null ? null : liquidity.liquid ? "Yes" : "No"} />
        <Row label="Most relevant market" mono value={liquidity.most_relevant_market} />
        <Row
          label="Average daily turnover"
          value={
            liquidity.avg_daily_turnover === null ? null : (
              <span className="num">
                {liquidity.turnover_currency
                  ? formatMoney(liquidity.avg_daily_turnover, liquidity.turnover_currency, { decimals: 0 })
                  : formatQuantity(liquidity.avg_daily_turnover)}
              </span>
            )
          }
        />
        <Row
          label="Average daily trades"
          value={liquidity.avg_daily_trades === null ? null : <span className="num">{formatQuantity(liquidity.avg_daily_trades)}</span>}
        />
      </dl>
    </Panel>
  );
}

function SourceLink({ href, label = "source" }: { href: string | null | undefined; label?: string }) {
  const url = safeHref(href);
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={cn("inline-flex items-center gap-1 text-xs", LINK)}>
      {label}
      <ExternalLinkIcon aria-hidden="true" className="size-3" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

function RelationshipMeta({ r }: { r: RelationshipOut }) {
  const sources = r.sources.length > 0 ? r.sources : [r.source];
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-text-muted">
      <span>{sources.map(sourceLabel).join(" + ")}</span>
      <span>{tierLabel(r.tier)}</span>
      {r.as_of ? <span className="num">as of {formatDate(r.as_of)}</span> : null}
      <SourceLink href={r.source_ref} />
    </span>
  );
}

function RelationshipItem({ r, label }: { r: RelationshipOut; label?: string }) {
  return (
    <li className="space-y-0.5 py-1.5">
      <p className="flex flex-wrap items-baseline gap-x-2">
        {label ? <span className="text-xs font-medium tracking-wide text-text-muted uppercase">{label}</span> : null}
        <span className="font-medium">{r.object_name}</span>
        {r.object_lei ? <LeiLink lei={r.object_lei} /> : null}
      </p>
      <RelationshipMeta r={r} />
    </li>
  );
}

function GroupStructure({ identity }: { identity: HoldingIdentity }) {
  const parents = sortParents(identity.parents);
  const related = groupRelated(identity.related);
  const [showSubs, setShowSubs] = useState(false);
  return (
    <Panel title="Group structure" note={CONSOLIDATION_NOTE}>
      <div className="grid gap-4 text-sm lg:grid-cols-2">
        <div>
          <h4 className="text-xs font-medium tracking-wide text-text-muted uppercase">Parents</h4>
          {parents.length > 0 ? (
            <ul className="divide-y divide-border">
              {parents.map((p) => (
                <RelationshipItem key={p.id} r={p} label={RELATIONSHIP_LABELS[p.type]} />
              ))}
            </ul>
          ) : identity.parent_exceptions.length > 0 ? (
            <ul className="mt-1 space-y-1">
              {identity.parent_exceptions.map((e) => (
                <li key={`${e.category}:${e.reason}`}>
                  {describeException(e)} <span className="text-xs text-text-muted">(stated by GLEIF)</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-text-muted">No consolidating parent reported.</p>
          )}
        </div>

        <div>
          <h4 className="text-xs font-medium tracking-wide text-text-muted uppercase">Owners</h4>
          {identity.owners.length > 0 ? (
            <ul className="divide-y divide-border">
              {identity.owners.map((o) => (
                <RelationshipItem key={o.id} r={o} />
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-text-muted">No owners on record.</p>
          )}
        </div>

        <div className="lg:col-span-2">
          <h4 className="text-xs font-medium tracking-wide text-text-muted uppercase">
            Subsidiaries <span className="num">({identity.subsidiary_count})</span>
          </h4>
          {identity.subsidiary_count === 0 ? (
            <p className="mt-1 text-text-muted">No subsidiaries reported.</p>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 -ml-2"
                aria-expanded={showSubs}
                aria-controls="subsidiary-list"
                onClick={() => setShowSubs((v) => !v)}
              >
                {showSubs ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
                {showSubs ? "Hide" : "Show"} subsidiaries
              </Button>
              {showSubs ? (
                <div id="subsidiary-list">
                  {identity.subsidiaries.length < identity.subsidiary_count ? (
                    <p className="num text-xs text-text-muted">
                      Showing {identity.subsidiaries.length} of {identity.subsidiary_count}.
                    </p>
                  ) : null}
                  <ul className="max-h-96 divide-y divide-border overflow-y-auto">
                    {identity.subsidiaries.map((s) => (
                      <RelationshipItem key={s.id} r={s} />
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="lg:col-span-2">
          <h4 className="text-xs font-medium tracking-wide text-text-muted uppercase">Competitors, suppliers and customers</h4>
          {related.length === 0 ? (
            <p className="mt-1 text-text-muted">None confirmed yet.</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-3">
              {related.map((g) => (
                <div key={g.type}>
                  <p className="mt-1 font-medium">{RELATIONSHIP_LABELS[g.type]}s</p>
                  <ul className="divide-y divide-border">
                    {g.items.map((r) => (
                      <RelationshipItem key={r.id} r={r} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function Suggestions({ identity, reload }: { identity: HoldingIdentity; reload: () => void }) {
  const [last, setLast] = useState<Decision | null>(null);
  return (
    <Panel
      title="Suggested relationships"
      note="Found only in a tier-3 source such as Wikidata, so not used until you confirm them. Confirming a competitor, supplier or customer adds it to the holding profile (a new config version) and the next news run matches on it; rejecting one means it is never suggested again."
      className={identity.suggested.length > 0 ? "border-info-signal/40" : undefined}
    >
      <DecisionMessage last={last} ticker={identity.ticker} />
      {identity.suggested.length === 0 ? (
        <p className="text-sm text-text-muted">Nothing waiting for a decision.</p>
      ) : (
        <ul className="divide-y divide-border">
          {identity.suggested.map((r) => (
            <SuggestionItem
              key={r.id}
              r={r}
              onDecided={(d) => {
                setLast(d);
                reload();
              }}
              onStale={reload}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Confirm or reject one suggested or disputed relationship. */
function useRelationshipDecision(r: RelationshipOut, onDecided: (d: Decision) => void, onStale: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(action: "confirm" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const api = getBrowserApiClient();
      const params = { params: { path: { relationship_id: r.id } } };
      const result =
        action === "confirm"
          ? await api.POST("/relationships/{relationship_id}/confirm", params)
          : await api.POST("/relationships/{relationship_id}/reject", params);
      if (result.data) {
        onDecided({ action, result: result.data });
        return;
      }
      if (result.response.status === 409) {
        setError("Already decided elsewhere; the list is reloaded.");
        onStale();
      } else {
        setError(`Not saved (HTTP ${result.response.status}).`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, decide };
}

function DecisionMessage({ last, ticker }: { last: Decision | null; ticker: string }) {
  return (
    <div role="status" aria-live="polite">
      {last ? (
        <p className="rounded-md border border-border bg-surface-muted p-2 text-sm">
          {decisionMessage(last.action, last.result)}
          {last.result.holding_profiles_version_id !== null ? (
            <>
              {" "}
              <Link href={`/config/holding-profiles#${encodeURIComponent(ticker)}`} className={LINK}>
                Open the profile
              </Link>{" "}
              ·{" "}
              <Link href={historyHref("holding-profiles")} className={LINK}>
                version history
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Relationships two sources disagree on: a tier-3 claim (Wikidata) beside the tier-1 source
 * (GLEIF) that contradicts it, with both sources and tiers. Neither is used until the owner
 * confirms the claim (it wins) or rejects it (the tier-1 version stands).
 */
function DisputedRelationships({ identity, reload }: { identity: HoldingIdentity; reload: () => void }) {
  const [last, setLast] = useState<Decision | null>(null);
  const disputed = identity.disputed ?? [];
  if (disputed.length === 0 && !last) return null;
  return (
    <Panel
      title="Disputed relationships"
      note="A claim from a community-edited source (tier 3) that a regulator or filing (tier 1) contradicts. Neither version is used until you decide."
      className={disputed.length > 0 ? "border-disputed/40" : undefined}
    >
      <DecisionMessage last={last} ticker={identity.ticker} />
      {disputed.length === 0 ? <p className="text-sm text-text-muted">Nothing disputed any more.</p> : null}
      <ul className="divide-y divide-border">
        {disputed.map((r) => (
          <DisputedItem
            key={r.id}
            r={r}
            onDecided={(d) => {
              setLast(d);
              reload();
            }}
            onStale={reload}
          />
        ))}
      </ul>
    </Panel>
  );
}

function DisputedItem({ r, onDecided, onStale }: { r: RelationshipOut; onDecided: (d: Decision) => void; onStale: () => void }) {
  const { busy, error, decide } = useRelationshipDecision(r, onDecided, onStale);
  const [confirmReject, setConfirmReject] = useState(false);
  const conflict = parseConflict(r.conflict);
  const choices = disputeChoices(r, conflict);
  const name = `${r.object_name} as ${RELATIONSHIP_LABELS[r.type].toLowerCase()}`;
  return (
    <li className="space-y-3 py-3 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium tracking-wide text-text-muted uppercase">{RELATIONSHIP_LABELS[r.type]}</span>
        <span className="inline-flex items-center gap-1 rounded-sm border border-disputed/40 bg-disputed-subtle px-1.5 py-0.5 text-xs font-medium">
          <ScaleIcon aria-hidden="true" className="size-3.5 text-disputed" />
          Disputed
        </span>
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <section aria-label="The claim" className="space-y-1 rounded-md border border-border p-3">
          <h4 className="text-xs font-medium text-text-muted">The claim</h4>
          <p className="font-medium">{r.object_name}</p>
          {r.object_lei ? <LeiLink lei={r.object_lei} /> : null}
          <RelationshipMeta r={r} />
        </section>
        <section aria-label="Contradicted by" className="space-y-1 rounded-md border border-disputed/40 p-3">
          <h4 className="text-xs font-medium text-text-muted">
            Contradicted by {conflict ? sourceLabel(conflict.source) : "another source"}
          </h4>
          {conflict ? (
            <>
              <p className="font-medium">{conflict.objectName}</p>
              {conflict.type && conflict.type !== r.type ? (
                <p className="text-xs text-text-muted">as {RELATIONSHIP_LABELS[conflict.type].toLowerCase()}</p>
              ) : null}
              <span className="inline-flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                <span>{sourceLabel(conflict.source)}</span>
                <span>{conflict.tier === null ? "Tier unknown" : tierLabel(conflict.tier)}</span>
                <SourceLink href={conflict.sourceRef} />
              </span>
            </>
          ) : (
            <p className="text-text-muted">The other source&rsquo;s version was not stored.</p>
          )}
        </section>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-text">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        {confirmReject ? (
          <>
            <span className="text-xs text-text-muted">{choices.reject}</span>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide("reject")} aria-label={`Reject ${name} for good`}>
              <XIcon aria-hidden="true" />
              Reject the claim
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmReject(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy} onClick={() => void decide("confirm")} aria-label={`Confirm ${name}`} title={choices.confirm}>
              {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
              Confirm the claim
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmReject(true)} aria-label={`Reject ${name}`} title={choices.reject}>
              <XIcon aria-hidden="true" />
              Reject the claim
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function SuggestionItem({
  r,
  onDecided,
  onStale,
}: {
  r: RelationshipOut;
  onDecided: (d: Decision) => void;
  onStale: () => void;
}) {
  const [confirmReject, setConfirmReject] = useState(false);
  const { busy, error, decide } = useRelationshipDecision(r, onDecided, onStale);

  const name = `${r.object_name} as ${RELATIONSHIP_LABELS[r.type].toLowerCase()}`;
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-2">
      <div className="min-w-0 space-y-0.5 text-sm">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-xs font-medium tracking-wide text-text-muted uppercase">{RELATIONSHIP_LABELS[r.type]}</span>
          <span className="font-medium">{r.object_name}</span>
          <span className="rounded-sm border border-dashed border-border px-1 text-xs text-text-muted">suggested</span>
        </p>
        <RelationshipMeta r={r} />
        {error ? (
          <p role="alert" className="text-xs text-text">
            {error}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {confirmReject ? (
          <>
            <span className="text-xs text-text-muted">Never suggest again?</span>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide("reject")} aria-label={`Reject ${name} for good`}>
              <XIcon aria-hidden="true" />
              Reject
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmReject(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" disabled={busy} onClick={() => void decide("confirm")} aria-label={`Confirm ${name}`}>
              {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
              Confirm
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmReject(true)} aria-label={`Reject ${name}`}>
              <XIcon aria-hidden="true" />
              Reject
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function WikidataPanel({ summary }: { summary: WikidataSummary }) {
  const rows: [string, string[]][] = [
    ["Industry", summary.industry],
    ["Products", summary.products],
    ["CEO", summary.ceo],
    ["Chair", summary.chair],
    ["Part of", summary.partOf],
  ];
  const website = safeHref(summary.website);
  return (
    <Panel
      title="Wikidata"
      note={
        <span className="num">
          Tier 3, community-edited: used for aliases, suggestions and to help verify management changes, never as data.
          {summary.fetchedAt ? <> Fetched {formatDateTime(summary.fetchedAt)}.</> : null}
        </span>
      }
    >
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        <Row label="Item" value={<SourceLink href={wikidataHref(summary.qid)} label={summary.qid} />} />
        {rows
          .filter(([, values]) => values.length > 0)
          .map(([label, values]) => (
            <Row key={label} label={label} value={values.join(" · ")} />
          ))}
        {website ? <Row label="Website" value={<SourceLink href={website} label={website} />} /> : null}
      </dl>
    </Panel>
  );
}
