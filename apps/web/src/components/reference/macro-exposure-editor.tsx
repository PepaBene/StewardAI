"use client";

import { useCallback, useMemo, useState } from "react";

import { useMutation } from "@/components/reference/action-bits";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS } from "@/components/reference/form-bits";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { emptyExposureDraft, exposureRequest, mappedShare, validateExposure, type ExposureDraft } from "@/lib/reference/endpoint-forms";
import { isRecord, num, recs } from "@/lib/parse-untyped";

const BASIS: Record<ExposureDraft["basis"], string> = { reported: "Reported by the company", estimated: "Estimated", user_assigned: "Assigned by you" };

/**
 * GET/POST/DELETE /macro/exposures: maps an issuer's revenue to countries (share of revenue by
 * region) or to products (HS code). The two are separate denominators and are never added
 * together. Whatever share is not mapped stays unknown: nothing here fills the remainder.
 */
export function MacroExposureEditor({ ticker }: { ticker: string }) {
  const contextFetcher = useCallback(() => getBrowserApiClient().GET("/macro/context/{ticker}", { params: { path: { ticker }, query: {} } }), [ticker]);
  const ctx = useApiResource(contextFetcher);
  const listFetcher = useCallback(() => getBrowserApiClient().GET("/macro/exposures"), []);
  const list = useApiResource(listFetcher);

  const issuerId = useMemo(() => {
    const d = ctx.state.kind === "ok" ? ctx.state.data : ctx.state.kind === "error" ? ctx.state.previous : null;
    if (!isRecord(d)) return null;
    for (const e of recs((d as Record<string, unknown>).effects)) {
      const n = num(e.issuer_id);
      if (n !== null) return n;
    }
    return null;
  }, [ctx.state]);

  const all = list.state.kind === "ok" ? list.state.data : list.state.kind === "error" ? list.state.previous : null;
  const [issuerText, setIssuerText] = useState("");
  const effectiveIssuer = issuerText.trim() !== "" ? Number(issuerText.trim()) : issuerId;
  const rows = useMemo(() => (all ?? []).filter((r) => effectiveIssuer === null || r.issuer_id === effectiveIssuer), [all, effectiveIssuer]);
  const share = useMemo(() => mappedShare(rows), [rows]);
  const refresh = () => {
    list.reload();
    ctx.reload();
  };

  return (
    <Blueprint id="exposure-editor" aria-labelledby="exposure-heading" className="flex min-w-0 scroll-mt-24 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Macro · your mapping" title={<span id="exposure-heading">Exposure editor</span>} />
      <p className="max-w-[68ch] text-sm text-text-muted">
        Map where {ticker} earns its revenue. A region row is a share of the issuer&apos;s total revenue; a product row is a share of its total revenue by HS
        product code. The two sets are kept apart and never added together. Any share you leave unmapped stays unknown, not zero.
      </p>

      <div className="flex flex-wrap items-end gap-4">
        <Field label="Issuer id" hint={issuerId !== null && issuerText.trim() === "" ? `Found from the macro context: ${issuerId}` : "Shown when macro context knows it; otherwise enter it"} className="w-56">
          <Input inputMode="numeric" value={issuerText} onChange={(e) => setIssuerText(e.target.value)} placeholder={issuerId !== null ? String(issuerId) : "Issuer id"} />
        </Field>
      </div>

      {list.state.kind === "loading" && !all ? <p className="text-sm text-text-muted">Loading mappings…</p> : null}
      {list.state.kind === "error" && !all ? <p className="text-sm text-text-muted">Mappings unavailable: {list.state.title}</p> : null}

      {all ? (
        rows.length === 0 ? (
          <p className="max-w-[68ch] border border-dashed border-border px-3 py-2 text-sm text-text-muted">
            No mapping recorded{effectiveIssuer !== null ? ` for issuer ${effectiveIssuer}` : ""}. Its exposure to any country or product is unknown.
          </p>
        ) : (
          <>
            <div className="relative overflow-x-auto border border-border">
              <table className="w-full min-w-[44rem] text-sm">
                <caption className="sr-only">Revenue mapping for the issuer</caption>
                <thead className="bg-surface-muted">
                  <tr className="border-b border-border text-xs text-text-muted">
                    <th scope="col" className="px-3 py-2 text-left font-medium">Segment</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Mapped to</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Share of revenue</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Basis</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Valid</th>
                    <th scope="col" className="px-3 py-2"><span className="sr-only">Remove</span></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <MappingRow key={r.id} row={r} onRemoved={refresh} />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-text-muted">
              By region: {share.geography > 0 ? formatPct(share.geography) : "none"} of revenue mapped, {share.geography > 0 && !share.geographyOver ? `${formatPct(1 - share.geography)} unknown` : share.geographyOver ? "over 100 percent, check the rows" : "all unknown"}. By product:{" "}
              {share.product > 0 ? formatPct(share.product) : "none"} mapped
              {share.product > 0 && !share.productOver ? `, ${formatPct(1 - share.product)} unknown` : share.productOver ? ", over 100 percent, check the rows" : ", all unknown"}.
            </p>
          </>
        )
      ) : null}

      <AddMapping defaultIssuer={effectiveIssuer !== null && Number.isFinite(effectiveIssuer) ? String(effectiveIssuer) : ""} onSaved={refresh} />
    </Blueprint>
  );
}

type Row = components["schemas"]["ExposureResult"];

function MappingRow({ row, onRemoved }: { row: Row; onRemoved: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const { state, run } = useMutation();
  const w = num(row.weight);
  return (
    <tr className="border-b border-border align-top last:border-0">
      <th scope="row" className="px-3 py-2 text-left font-normal">{row.segment}</th>
      <td className="px-3 py-2">
        {row.hs_code ? `Product ${row.hs_code} (${row.hs_revision ?? "revision unknown"})` : row.geography ? `Region ${row.geography}` : "Unknown"}
      </td>
      <td className="num px-3 py-2 text-right">{w === null ? "Unknown" : formatPct(w)}</td>
      <td className="px-3 py-2">{row.basis === "user_assigned" ? <OwnerTag>{BASIS.user_assigned}</OwnerTag> : BASIS[row.basis]}</td>
      <td className="px-3 py-2 text-xs">
        {formatDate(row.valid_from)}
        {row.valid_to ? ` to ${formatDate(row.valid_to)}` : " onward"}
      </td>
      <td className="px-3 py-2 text-right">
        {confirm ? (
          <span className="inline-flex items-center gap-2">
            <Button type="button" size="sm" disabled={state.kind === "saving"} onClick={() => run(() => getBrowserApiClient().DELETE("/macro/exposures/{mapping_id}", { params: { path: { mapping_id: row.id } } }), () => "Removed.", onRemoved)}>
              Remove mapping
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirm(false)}>Keep</Button>
          </span>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => setConfirm(true)}>Remove</Button>
        )}
        <SaveNote state={state} />
      </td>
    </tr>
  );
}

function AddMapping({ defaultIssuer, onSaved }: { defaultIssuer: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ExposureDraft>(() => emptyExposureDraft(defaultIssuer));
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const d = open && draft.issuerId === "" && defaultIssuer ? { ...draft, issuerId: defaultIssuer } : draft;
  const errors = validateExposure(d);
  const set = <K extends keyof ExposureDraft>(k: K, v: ExposureDraft[K]) => setDraft({ ...d, [k]: v });
  const show = (k: keyof ExposureDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    const ok = await run(
      () => getBrowserApiClient().POST("/macro/exposures", { body: exposureRequest(d) }),
      () => `Saved the ${d.kind === "geography" ? "region" : "product"} mapping for ${d.segment.trim()}.`,
      onSaved,
    );
    if (ok) {
      setDraft(emptyExposureDraft(d.issuerId));
      setTouched(false);
    }
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>Map revenue to a region or product</Button>
        <SaveNote state={state} />
      </div>
    );
  }
  return (
    <form onSubmit={submit} noValidate aria-label="Map revenue">
      <FormFrame title="Map a revenue segment">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Issuer id" error={show("issuerId")}>
            <Input inputMode="numeric" value={d.issuerId} onChange={(e) => set("issuerId", e.target.value)} />
          </Field>
          <Field label="Segment name" error={show("segment")} hint="As the company reports it">
            <Input value={d.segment} onChange={(e) => set("segment", e.target.value)} maxLength={160} />
          </Field>
          <Field label="Map by" hint="Region and product shares are separate denominators">
            <select className={SELECT_CLASS} value={d.kind} onChange={(e) => set("kind", e.target.value as ExposureDraft["kind"])}>
              <option value="geography">Region, share of total revenue</option>
              <option value="product">Product, share of total revenue</option>
            </select>
          </Field>
          {d.kind === "geography" ? (
            <Field label="Country code" error={show("geography")} hint="For example US, DE">
              <Input value={d.geography} onChange={(e) => set("geography", e.target.value)} maxLength={5} />
            </Field>
          ) : (
            <>
              <Field label="HS code" error={show("hsCode")} hint="2 to 6 digits">
                <Input value={d.hsCode} onChange={(e) => set("hsCode", e.target.value)} maxLength={6} />
              </Field>
              <Field label="HS revision" error={show("hsRevision")}>
                <Input value={d.hsRevision} onChange={(e) => set("hsRevision", e.target.value)} maxLength={20} />
              </Field>
            </>
          )}
          <Field label="Share of revenue (%)" error={show("weightPct")} hint="0 to 100">
            <Input inputMode="decimal" value={d.weightPct} onChange={(e) => set("weightPct", e.target.value)} />
          </Field>
          <Field label="Basis">
            <select className={SELECT_CLASS} value={d.basis} onChange={(e) => set("basis", e.target.value as ExposureDraft["basis"])}>
              {(Object.keys(BASIS) as ExposureDraft["basis"][]).map((b) => (
                <option key={b} value={b}>{BASIS[b]}</option>
              ))}
            </select>
          </Field>
          <Field label="Valid from" error={show("validFrom")} hint="YYYY-MM-DD">
            <Input value={d.validFrom} onChange={(e) => set("validFrom", e.target.value)} />
          </Field>
          <Field label="Valid to" error={show("validTo")} hint="Optional">
            <Input value={d.validTo} onChange={(e) => set("validTo", e.target.value)} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={state.kind === "saving"}>Save mapping</Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>Close</Button>
          <SaveNote state={state} />
        </div>
      </FormFrame>
    </form>
  );
}
