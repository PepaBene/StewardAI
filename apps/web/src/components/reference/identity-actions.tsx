"use client";

import { useCallback, useState } from "react";

import { KeyRows, useMutation } from "@/components/reference/action-bits";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS } from "@/components/reference/form-bits";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cellText, emptyOverrideDraft, overrideRequest, validateListingDecision, validateOverride, type OverrideDraft } from "@/lib/reference/endpoint-forms";
import { titleCase } from "@/lib/parse-untyped";

/**
 * Listing match and identity decisions for one holding: GET /holdings/{ticker}/listing, POST
 * .../listing/derive and .../listing/confirm, and POST /reference/identity/{ticker}/override.
 * A derived match stays "suggested" until all sources agree or you confirm it; your decision is
 * never overwritten by a later derive.
 */
export function IdentityActions({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/listing", { params: { path: { ticker } } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const derive = useMutation();

  // Typed ListingOverviewResponse; a missing figure reads "Unknown", never 0.
  const rows = data
    ? [
        { label: "Status", value: titleCase(data.status) },
        { label: "Match score", value: cellText(data.score) },
        { label: "Decided at", value: cellText(data.decided_at) },
        { label: "Listing", value: data.listing ? [data.listing.mic, data.listing.ticker, data.listing.currency].filter(Boolean).join(" · ") || `#${data.listing.id}` : "No listing chosen" },
        { label: "ISIN", value: cellText(data.listing?.isin) },
        ...data.provider_symbols.map((p) => ({ label: `${titleCase(p.provider)} symbol`, value: `${p.identifier}${p.match_status ? ` (${p.match_status.replaceAll("_", " ")})` : ""}` })),
      ]
    : [];

  return (
    <Blueprint aria-labelledby="listing-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Identity · your decisions" title={<span id="listing-heading">Listing match and identity overrides</span>} />
      <p className="max-w-[68ch] text-sm text-text-muted">
        Corporate actions and the fallback price sources only run once the listing match is confirmed. Deriving looks the instrument up in the instrument
        record, ESMA FIRDS and OpenFIGI and never overwrites a decision you made.
      </p>

      {state.kind === "loading" && !data ? <p className="text-sm text-text-muted">Loading listing…</p> : null}
      {state.kind === "error" && !data ? <p className="text-sm text-text-muted">No listing is derived for {ticker} yet ({state.title}). Derive it below.</p> : null}
      {rows.length > 0 ? <KeyRows rows={rows} /> : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={derive.state.kind === "saving"}
          onClick={() =>
            derive.run(
              () => getBrowserApiClient().POST("/holdings/{ticker}/listing/derive", { params: { path: { ticker } } }),
              (d) => `Derived: ${cellText(d.status)}${d.reason ? `. ${d.reason}` : ""}`,
              reload,
            )
          }
        >
          Derive the listing
        </Button>
        <SaveNote state={derive.state} idle={<span>Reads public registers; no paid plan is used.</span>} />
      </div>

      <ListingDecision ticker={ticker} onSaved={reload} />
      <OverrideForm ticker={ticker} onSaved={reload} />
    </Blueprint>
  );
}

function ListingDecision({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [decision, setDecision] = useState<"confirmed" | "rejected">("confirmed");
  const [reason, setReason] = useState("");
  const [listingId, setListingId] = useState("");
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const errors = validateListingDecision(reason, listingId);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    const ok = await run(
      () =>
        getBrowserApiClient().POST("/holdings/{ticker}/listing/confirm", {
          params: { path: { ticker } },
          body: { decision, reason: reason.trim(), listing_id: listingId.trim() === "" ? null : Number(listingId) },
        }),
      () => `Listing match ${decision} for ${ticker}.`,
      onSaved,
    );
    if (ok) {
      setReason("");
      setListingId("");
      setTouched(false);
    }
  }
  return (
    <form onSubmit={submit} noValidate aria-label="Confirm or reject the listing match">
      <FormFrame title="Your decision on the derived listing">
        <div className="grid gap-3 sm:grid-cols-[1fr_2fr_1fr]">
          <Field label="Decision">
            <select className={SELECT_CLASS} value={decision} onChange={(e) => setDecision(e.target.value as "confirmed" | "rejected")}>
              <option value="confirmed">Confirm the match</option>
              <option value="rejected">Reject the match</option>
            </select>
          </Field>
          <Field label="Reason" error={touched ? errors.reason : undefined} hint="At least 10 characters. Recorded as yours.">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
          </Field>
          <Field label="Another listing id" error={touched ? errors.listingId : undefined} hint="Optional">
            <Input inputMode="numeric" value={listingId} onChange={(e) => setListingId(e.target.value)} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={state.kind === "saving"}>
            Save decision
          </Button>
          <OwnerTag />
          <SaveNote state={state} />
        </div>
      </FormFrame>
    </form>
  );
}

function OverrideForm({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [draft, setDraft] = useState<OverrideDraft>(emptyOverrideDraft);
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const errors = validateOverride(draft);
  const set = <K extends keyof OverrideDraft>(k: K, v: OverrideDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof OverrideDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    const ok = await run(
      () => getBrowserApiClient().POST("/reference/identity/{ticker}/override", { params: { path: { ticker } }, body: overrideRequest(draft) }),
      () => `Identity decision (${draft.decision}) saved for ${ticker}.`,
      onSaved,
    );
    if (ok) {
      setDraft(emptyOverrideDraft());
      setTouched(false);
    }
  }
  return (
    <form onSubmit={submit} noValidate aria-label="Override an identity decision">
      <FormFrame title="Override an identity match">
        <p className="max-w-[68ch] text-xs text-text-muted">Record your own decision on an issuer, security or listing. Fill in at least one id.</p>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Decision">
            <select className={SELECT_CLASS} value={draft.decision} onChange={(e) => set("decision", e.target.value as OverrideDraft["decision"])}>
              <option value="confirmed">Confirmed</option>
              <option value="rejected">Rejected</option>
              <option value="unresolved">Unresolved</option>
            </select>
          </Field>
          <Field label="Issuer id" error={show("issuerId")}>
            <Input inputMode="numeric" value={draft.issuerId} onChange={(e) => set("issuerId", e.target.value)} />
          </Field>
          <Field label="Security id" error={show("securityId")}>
            <Input inputMode="numeric" value={draft.securityId} onChange={(e) => set("securityId", e.target.value)} />
          </Field>
          <Field label="Listing id" error={show("listingId")}>
            <Input inputMode="numeric" value={draft.listingId} onChange={(e) => set("listingId", e.target.value)} />
          </Field>
        </div>
        <Field label="Reason" error={show("reason")} hint="Up to 500 characters">
          <Input value={draft.reason} onChange={(e) => set("reason", e.target.value)} maxLength={500} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={state.kind === "saving"}>
            Save override
          </Button>
          <OwnerTag />
          <SaveNote state={state} />
        </div>
      </FormFrame>
    </form>
  );
}
