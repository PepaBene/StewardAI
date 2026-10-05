"use client";

import { useState } from "react";

import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS, TEXTAREA_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { emptyFactsheetDraft, factsheetRequest, validateFactsheet, type FactsheetDraft } from "@/lib/funds/forms";
import { isoToday } from "@/lib/reference/calendar";
import { apiErrorText } from "@/lib/reference/form-utils";

/**
 * Enter factsheet figures from the KID or issuer factsheet where the issuer feed leaves gaps
 * (PUT /funds/{isin}/factsheet). Rows saved here are labelled as entered by you; blank fields
 * are stored as not published, never as 0.
 */
export function FactsheetEditor({ isin, onSaved }: { isin: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const today = isoToday();
  const [draft, setDraft] = useState<FactsheetDraft>(() => emptyFactsheetDraft(today));
  const [touched, setTouched] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validateFactsheet(draft, today);
  const set = <K extends keyof FactsheetDraft>(k: K, v: FactsheetDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof FactsheetDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().PUT("/funds/{isin}/factsheet", { params: { path: { isin } }, body: factsheetRequest(draft) });
      if (r.response.ok) {
        setSave({ kind: "ok", text: `Saved the factsheet for ${draft.asOf}. It is listed below as entered by you.` });
        setTouched(false);
        onSaved();
      } else setSave({ kind: "error", text: apiErrorText(r.error, r.response.status) });
    } catch (err) {
      setSave({ kind: "error", text: `Not saved: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
          Enter figures by hand
        </Button>
        <SaveNote state={save} idle={<span>For gaps the issuer feed leaves, from the KID or the issuer factsheet.</span>} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Enter factsheet figures for ${isin}`}>
      <FormFrame title={`Factsheet figures for ${isin}`}>
        <p className="max-w-[68ch] text-xs text-text-muted">
          <OwnerTag>Entered by you</OwnerTag> These rows are kept apart from what the issuer feed reported and are labelled as yours. Leave a field blank if the document does not state it.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Figures valid as of" error={show("asOf")}>
            <Input type="date" max={today} value={draft.asOf} onChange={(e) => set("asOf", e.target.value)} />
          </Field>
          <Field label="Annual charge, % of fund value" hint="Type 0.07 for 0.07% a year. Under 20." error={show("expensePct")}>
            <Input inputMode="decimal" value={draft.expensePct} onChange={(e) => set("expensePct", e.target.value)} className="num" />
          </Field>
          <Field label="Distribution" error={show("distribution")}>
            <select value={draft.distribution} onChange={(e) => set("distribution", e.target.value as FactsheetDraft["distribution"])} className={SELECT_CLASS}>
              <option value="">Not stated</option>
              <option value="accumulating">Accumulating</option>
              <option value="distributing">Distributing</option>
            </select>
          </Field>
          <Field label="Fund size" hint="Amount in the fund's own currency." error={show("totalAssets")}>
            <Input inputMode="decimal" value={draft.totalAssets} onChange={(e) => set("totalAssets", e.target.value)} className="num" />
          </Field>
          <Field label="Fund size currency" error={show("currency")}>
            <Input value={draft.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} maxLength={3} placeholder="EUR" className="num" />
          </Field>
          <Field label="Share class" hint="Up to 80 characters." error={show("shareClass")}>
            <Input value={draft.shareClass} onChange={(e) => set("shareClass", e.target.value)} maxLength={80} />
          </Field>
          <Field label="Benchmark" hint="Up to 200 characters." error={show("benchmark")} className="sm:col-span-2">
            <Input value={draft.benchmark} onChange={(e) => set("benchmark", e.target.value)} maxLength={200} />
          </Field>
          <Field label="NAV basis" hint="Up to 200 characters." error={show("navBasis")}>
            <Input value={draft.navBasis} onChange={(e) => set("navBasis", e.target.value)} maxLength={200} />
          </Field>
        </div>
        <Field label="Where you read this" hint={`Optional note, ${draft.note.trim().length} of 1000 characters.`} error={show("note")}>
          <textarea value={draft.note} onChange={(e) => set("note", e.target.value)} maxLength={1000} className={TEXTAREA_CLASS} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.kind === "saving"}>
            Save factsheet
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
            Close
          </Button>
          <SaveNote state={save} />
        </div>
      </FormFrame>
    </form>
  );
}
