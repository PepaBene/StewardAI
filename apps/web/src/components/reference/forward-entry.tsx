"use client";

import { useState } from "react";

import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { apiErrorText } from "@/lib/reference/form-utils";
import { emptyForwardDraft, ENTRY_KINDS, forwardRequest, validateForward, type ForwardDraft } from "@/lib/reference/forms";

/**
 * Record company guidance, a scenario of your own or a calendar estimate for a holding
 * (POST /holdings/{ticker}/forward-data). These are listed under Guidance and scenarios, apart
 * from published analyst consensus, and are never blended into it.
 */
export function ForwardEntry({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ForwardDraft>(emptyForwardDraft);
  const [touched, setTouched] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validateForward(draft);
  const set = <K extends keyof ForwardDraft>(k: K, v: ForwardDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof ForwardDraft) => (touched ? errors[k] : undefined);
  const kind = ENTRY_KINDS.find((k) => k.id === draft.kind) ?? ENTRY_KINDS[0];
  const isScenario = draft.kind === "owner_scenario";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().POST("/holdings/{ticker}/forward-data", { params: { path: { ticker } }, body: forwardRequest(draft) });
      if (r.response.ok) {
        setSave({ kind: "ok", text: `Saved ${kind.label.toLowerCase()} for ${draft.fiscalPeriod.trim()}. It appears under Guidance and scenarios.` });
        setDraft(emptyForwardDraft());
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
          Record guidance or a scenario
        </Button>
        <SaveNote state={save} idle={<span>Analyst consensus comes from providers and cannot be entered here.</span>} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Record guidance or a scenario for ${ticker}`}>
      <FormFrame title={`Record a forward figure for ${ticker}`}>
        <p className="max-w-[68ch] text-xs text-text-muted">
          <OwnerTag>Entered by you</OwnerTag> {kind.hint} Enter a point value, a low and high range, or both. Leave a field blank if it was not stated; blank is never read as 0.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Kind" className="sm:col-span-2">
            <select value={draft.kind} onChange={(e) => set("kind", e.target.value as ForwardDraft["kind"])} className={SELECT_CLASS}>
              {ENTRY_KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label={isScenario ? "Source" : "Who published it"}
            hint={isScenario ? "Recorded as you." : "The company or filing, such as ACME Q3 release."}
            error={show("provider")}
            className="sm:col-span-2"
          >
            <Input value={isScenario ? "owner" : draft.provider} disabled={isScenario} onChange={(e) => set("provider", e.target.value)} maxLength={80} />
          </Field>
          <Field label="Metric" hint="For example revenue or eps_diluted." error={show("metric")}>
            <Input value={draft.metric} onChange={(e) => set("metric", e.target.value)} maxLength={120} />
          </Field>
          <Field label="Fiscal period" hint="For example FY2027 or FY2026Q4." error={show("fiscalPeriod")}>
            <Input value={draft.fiscalPeriod} onChange={(e) => set("fiscalPeriod", e.target.value)} maxLength={40} className="num" />
          </Field>
          <Field label="Period ends">
            <Input type="date" value={draft.periodEnd} onChange={(e) => set("periodEnd", e.target.value)} />
          </Field>
          <Field label="Currency" hint="Blank for a ratio or count." error={show("currency")}>
            <Input value={draft.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} maxLength={3} placeholder="USD" className="num" />
          </Field>
          <Field label="Point value" error={show("value")}>
            <Input inputMode="decimal" value={draft.value} onChange={(e) => set("value", e.target.value)} className="num" />
          </Field>
          <Field label="Range low" error={show("low")}>
            <Input inputMode="decimal" value={draft.low} onChange={(e) => set("low", e.target.value)} className="num" />
          </Field>
          <Field label="Range high" error={show("high")}>
            <Input inputMode="decimal" value={draft.high} onChange={(e) => set("high", e.target.value)} className="num" />
          </Field>
          <Field label="Basis" hint="For example adjusted or GAAP." error={show("basis")}>
            <Input value={draft.basis} onChange={(e) => set("basis", e.target.value)} maxLength={120} />
          </Field>
          <Field label="Published on" hint="The date the figure was issued.">
            <Input type="date" value={draft.publishedAt} onChange={(e) => set("publishedAt", e.target.value)} />
          </Field>
          <Field label="Source address" error={show("sourceUrl")} className="sm:col-span-2 lg:col-span-3">
            <Input type="url" value={draft.sourceUrl} onChange={(e) => set("sourceUrl", e.target.value)} placeholder="https://" maxLength={2000} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.kind === "saving"}>
            Save figure
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
