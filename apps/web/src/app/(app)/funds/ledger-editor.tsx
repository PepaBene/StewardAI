"use client";

import { useState } from "react";

import { FormFrame, Field, OwnerTag, SaveNote, SELECT_CLASS, TEXTAREA_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { boolToTri, emptyLedgerDraft, ledgerRequest, validateLedger, type LedgerDraft, type Tri } from "@/lib/funds/forms";
import { TIERS, type LedgerRow } from "@/lib/funds/ledger";
import { apiErrorText } from "@/lib/reference/form-utils";

export const draftFromRow = (r: LedgerRow): LedgerDraft => ({
  subjectKind: r.subject_kind,
  subjectKey: r.subject_key,
  label: r.label,
  tier: r.tier,
  reason: r.origin === "manual" ? r.reason : "",
  proxyIsin: r.proxy_fund_isin ?? "",
  totalReturn: boolToTri(r.total_return_available),
  historical: boolToTri(r.historical_constituents),
  sourceUrl: r.source_url ?? "",
});

const TRI: { id: Tri; label: string }[] = [
  { id: "unknown", label: "Unknown" },
  { id: "yes", label: "Yes" },
  { id: "no", label: "No" },
];

/**
 * Record or correct a ledger entry by hand (PUT /funds/coverage-ledger). An entry saved here is
 * marked as verified by you and a ledger refresh never overwrites it.
 */
export function LedgerEditor({ initial, onSaved, onClose }: { initial: LedgerDraft | null; onSaved: () => void; onClose: () => void }) {
  const [draft, setDraft] = useState<LedgerDraft>(initial ?? emptyLedgerDraft());
  const [touched, setTouched] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validateLedger(draft);
  const invalid = Object.keys(errors).length > 0;
  const set = <K extends keyof LedgerDraft>(k: K, v: LedgerDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof LedgerDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (invalid) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().PUT("/funds/coverage-ledger", { body: ledgerRequest(draft) });
      if (r.response.ok) {
        setSave({ kind: "ok", text: `Saved ${draft.label.trim()}. It is marked as verified by you.` });
        onSaved();
      } else setSave({ kind: "error", text: apiErrorText(r.error, r.response.status) });
    } catch (err) {
      setSave({ kind: "error", text: `Not saved: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Record a coverage ledger entry">
      <FormFrame title={initial ? `Correct ${initial.label}` : "Record where a fund or index gets its holdings"}>
        <p className="max-w-[68ch] text-xs text-text-muted">
          <OwnerTag>Entered by you</OwnerTag> An entry saved here is never overwritten by a ledger refresh. Saving with the same fund or index replaces your earlier entry.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Kind" error={show("subjectKind")}>
            <select value={draft.subjectKind} onChange={(e) => set("subjectKind", e.target.value as LedgerDraft["subjectKind"])} className={SELECT_CLASS}>
              <option value="fund">Fund</option>
              <option value="index">Index</option>
            </select>
          </Field>
          <Field label={draft.subjectKind === "fund" ? "Fund ISIN or key" : "Index symbol"} hint="1 to 80 characters." error={show("subjectKey")}>
            <Input value={draft.subjectKey} onChange={(e) => set("subjectKey", e.target.value)} maxLength={80} className="num" />
          </Field>
          <Field label="Name" hint="Up to 120 characters." error={show("label")}>
            <Input value={draft.label} onChange={(e) => set("label", e.target.value)} maxLength={120} />
          </Field>
          <Field label="Source tier" error={show("tier")} hint={TIERS.find((t) => t.id === draft.tier)?.meaning}>
            <select value={draft.tier} onChange={(e) => set("tier", e.target.value as LedgerDraft["tier"])} className={SELECT_CLASS}>
              {TIERS.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Proxy fund ISIN" hint={draft.tier === "proxy_fund" ? "Required for a proxy fund." : "Only for a proxy fund."} error={show("proxyIsin")}>
            <Input value={draft.proxyIsin} onChange={(e) => set("proxyIsin", e.target.value.toUpperCase())} placeholder="IE00B4L5Y983" maxLength={12} className="num" />
          </Field>
          <Field label="Source address" hint="Optional. Where you checked this." error={show("sourceUrl")}>
            <Input type="url" value={draft.sourceUrl} onChange={(e) => set("sourceUrl", e.target.value)} placeholder="https://" maxLength={2000} />
          </Field>
          <Field label="Total return series available" hint="Unknown stays unknown; it is not read as no.">
            <TriSelect value={draft.totalReturn} onChange={(v) => set("totalReturn", v)} />
          </Field>
          <Field label="Past constituents available" hint="Needed for a backtest without hindsight.">
            <TriSelect value={draft.historical} onChange={(v) => set("historical", v)} />
          </Field>
        </div>
        <Field label="Why this tier" hint={`${draft.reason.trim().length} of 1000 characters, at least 3.`} error={show("reason")}>
          <textarea value={draft.reason} onChange={(e) => set("reason", e.target.value)} maxLength={1000} className={TEXTAREA_CLASS} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.kind === "saving"}>
            Save entry
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
          <SaveNote state={save} />
        </div>
      </FormFrame>
    </form>
  );
}

function TriSelect({ value, onChange, ...rest }: { value: Tri; onChange: (v: Tri) => void } & Omit<React.ComponentProps<"select">, "value" | "onChange">) {
  return (
    <select {...rest} value={value} onChange={(e) => onChange(e.target.value as Tri)} className={SELECT_CLASS}>
      {TRI.map((t) => (
        <option key={t.id} value={t.id}>
          {t.label}
        </option>
      ))}
    </select>
  );
}
