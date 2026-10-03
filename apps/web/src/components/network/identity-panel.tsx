"use client";

import { useCallback } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter, StatePlaceholder } from "@/components/data-state";
import { AlertMessage } from "@/components/alert-message";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import type { HoldingIdentity, RelationshipOut } from "@/lib/api/client";
import { fetchLegalEntityHistory, fetchListingHistory } from "@/lib/network/client";
import { humanize, rowsState } from "@/lib/network/logic";
import type { RowStatus } from "@/lib/network/types";

import { RowStatusBadge, SourceLink } from "./badges";

function relRow(r: RelationshipOut) {
  const status = r.status as RowStatus;
  return (
    <li key={r.id} className="grid grid-cols-1 gap-x-3 gap-y-0.5 border-t border-border-soft py-1.5 text-sm first:border-0 sm:grid-cols-[8rem_minmax(0,1fr)_auto]">
      <span className="text-text-muted">{humanize(r.type)}</span>
      <span className="min-w-0">
        {r.object_name}
        <span className="block text-xs text-text-muted">
          {r.sources.join(", ") || r.source} · tier {r.tier} · {r.as_of ? `as of ${formatDate(r.as_of)}` : "no as-of date"}
          {r.object_lei ? ` · LEI ${r.object_lei}` : ""}
        </span>
        {r.status === "disputed" && r.conflict ? (
          <span className="block text-xs text-text">Contradicted by: {String(r.conflict.source ?? "another source")} ({String(r.conflict.object_name ?? "different entity")})</span>
        ) : null}
        {r.source_ref ? <SourceLink href={r.source_ref}>source</SourceLink> : null}
      </span>
      <RowStatusBadge status={status} />
    </li>
  );
}

/**
 * Legal structure from GLEIF level 2 and other registries: accounting consolidation and control
 * kept in their own block, owners (shareholders) in another. A parent here is never read as a
 * beneficial shareholder.
 */
export function IdentityPanel({ identity, asOfParam, asOfDate }: { identity: HoldingIdentity; asOfParam: string | undefined; asOfDate: string | null }) {
  const control = [...identity.parents, ...identity.subsidiaries.slice(0, 12)];
  const lei = identity.legal_entity?.lei ?? null;
  const all = [...identity.parents, ...identity.subsidiaries, ...identity.owners];
  return (
    <Blueprint as="section" aria-labelledby="network-identity-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Legal structure" title={<span id="network-identity-heading">Consolidation, control and identity</span>} />
      {asOfDate ? (
        <p className="border-l-2 border-border pl-3 text-xs text-text">
          Current structure, not as of {formatDate(asOfDate)}: the parents, subsidiaries and owners below are today&apos;s confirmed records. Only the two history lists are limited to that date.
        </p>
      ) : null}
      <p className="max-w-prose text-sm text-text">
        {identity.legal_entity
          ? `${identity.legal_entity.legal_name} (LEI ${identity.legal_entity.lei}): ${identity.parents.length} consolidating parent${identity.parents.length === 1 ? "" : "s"}, ${identity.subsidiary_count} subsidiar${identity.subsidiary_count === 1 ? "y" : "ies"}.`
          : "No legal entity (LEI) is resolved for this holding, so no GLEIF structure can be shown."}
      </p>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold">Accounting consolidation and control</h3>
          <p className="text-xs text-text-muted">GLEIF level 2 parents and subsidiaries. Consolidation is not beneficial ownership.</p>
          {control.length ? <ul className="mt-1">{control.map(relRow)}</ul> : <p className="mt-1 text-xs text-text-muted">None recorded.</p>}
          {identity.subsidiary_count > identity.subsidiaries.slice(0, 12).length ? <p className="text-xs text-text-muted">{identity.subsidiary_count - 12} more subsidiaries are not listed.</p> : null}
          {identity.parent_exceptions.length ? (
            <ul className="mt-1 text-xs text-text-muted">
              {identity.parent_exceptions.map((x) => (
                <li key={x.category}>No parent reported: {humanize(x.category)} ({x.reason}).</li>
              ))}
            </ul>
          ) : null}
        </div>
        <div>
          <h3 className="text-sm font-semibold">Owners</h3>
          <p className="text-xs text-text-muted">Shareholders recorded from registries; a separate channel from consolidation.</p>
          {identity.owners.length ? <ul className="mt-1">{identity.owners.map(relRow)}</ul> : <p className="mt-1 text-xs text-text-muted">None recorded.</p>}
        </div>
      </div>
      {lei ? <LeiHistory lei={lei} asOfParam={asOfParam} /> : null}
      {identity.isin ? <ListingHistory isin={identity.isin} asOfParam={asOfParam} /> : null}
      <ProvenanceFooter
        provenance={{
          source: "GLEIF level 1 and 2, Wikidata (tier 3), registries",
          // The structure itself is always current; stamping it with the picked date would be false.
          asOf: null,
          method: "GLEIF and filings are tier 1; Wikidata is tier 3 and cannot establish ownership. Owner decisions win.",
          state: all.length === 0 ? "not_covered" : rowsState(all.map((r) => ({ status: r.status as RowStatus }))),
          reason: identity.identity_error,
        }}
      />
    </Blueprint>
  );
}

export function LeiHistory({ lei, asOfParam }: { lei: string; asOfParam: string | undefined }) {
  const fetcher = useCallback(() => fetchLegalEntityHistory(lei, asOfParam), [lei, asOfParam]);
  const { state } = useApiResource(fetcher);
  return (
    <div>
      <h3 className="text-sm font-semibold">Legal-entity history (names, status, successor)</h3>
      {state.kind === "loading" ? <p role="status" className="text-xs text-text-muted">Loading…</p> : null}
      {state.kind === "error" ? <AlertMessage title={state.title}>{state.detail}</AlertMessage> : null}
      {state.kind === "ok" && state.data.length === 0 ? <StatePlaceholder state="not_covered" reason="No GLEIF snapshot is stored at or before this date." minHeight={60} /> : null}
      {state.kind === "ok" && state.data.length > 0 ? (
        <ul className="mt-1">
          {state.data.map((h, i) => (
            <li key={`${i}|${h.lei}|${h.known_at}`} className="border-t border-border-soft py-1.5 text-sm first:border-0">
              <span className="font-medium">{h.legal_name ?? "name not stated"}</span>
              <span className="num block text-xs text-text-muted">
                known {formatDate(h.known_at.slice(0, 10))} · valid {h.valid_from ?? "?"} to {h.valid_to ?? "open"} · {h.entity_status ?? "status ?"} / {h.registration_status ?? "registration ?"}
                {h.successor_lei ? ` · successor LEI ${h.successor_lei}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ListingHistory({ isin, asOfParam }: { isin: string; asOfParam: string | undefined }) {
  const fetcher = useCallback(() => fetchListingHistory(isin, asOfParam), [isin, asOfParam]);
  const { state } = useApiResource(fetcher);
  return (
    <div>
      <h3 className="text-sm font-semibold">Listing lifecycle for {isin}</h3>
      {state.kind === "loading" ? <p role="status" className="text-xs text-text-muted">Loading…</p> : null}
      {state.kind === "error" ? <AlertMessage title={state.title}>{state.detail}</AlertMessage> : null}
      {state.kind === "ok" && state.data.length === 0 ? <StatePlaceholder state="not_covered" reason="No listing lifecycle snapshot is stored for this ISIN at this date." minHeight={60} /> : null}
      {state.kind === "ok" && state.data.length > 0 ? (
        <ul className="mt-1">
          {state.data.map((h, i) => (
            // ESMA publishes several records for one venue and date (a modification beside the unchanged
            // record, one per trading-venue identifier): the position keeps the rows apart.
            <li key={`${i}|${h.mic}|${h.publication_date}|${h.known_at}`} className="num border-t border-border-soft py-1.5 text-xs first:border-0">
              {h.mic} · {h.status ?? "status ?"} · published {h.publication_date ?? "?"}
              {h.terminated_at ? ` · terminated ${formatDate(h.terminated_at)}` : ""}
              {h.issuer_lei ? ` · issuer LEI ${h.issuer_lei}` : ""} · <SourceLink href={h.source_url}>source</SourceLink>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
