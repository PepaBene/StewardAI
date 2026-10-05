"use client";

import { useState } from "react";

import { QuotaButton, QueuedJob, useMutation } from "@/components/reference/action-bits";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { emptyPatentDraft, patentRequest, validatePatent, type PatentDraft } from "@/lib/reference/endpoint-forms";

const BASIS_LABEL: Record<PatentDraft["basis"], string> = {
  suggested: "Suggested, not confirmed",
  direct: "Direct, with ownership evidence",
  dated_subsidiary_path: "Through a dated subsidiary edge",
};

/**
 * POST /holdings/{ticker}/patent-families/epo-ops/refresh (spends EPO OPS free-tier quota) and
 * POST /holdings/{ticker}/patent-families (a family you record yourself, marked as yours).
 */
export function PatentActions({ ticker, onChanged }: { ticker: string; onChanged: () => void }) {
  const refresh = useMutation();
  const [queued, setQueued] = useState<unknown>(null);
  return (
    <section aria-label="Patent data entry" className="space-y-3 border-t border-border-soft pt-3">
      <QuotaButton
        label="Refresh from EPO"
        provider="EPO OPS"
        cost="one or more requests of the weekly free quota"
        busy={refresh.state.kind === "saving"}
        onConfirm={() =>
          refresh.run(
            () => getBrowserApiClient().POST("/holdings/{ticker}/patent-families/epo-ops/refresh", { params: { path: { ticker } } }),
            () => "EPO refresh requested.",
            (d) => {
              setQueued(d);
              onChanged();
            },
          )
        }
      />
      <SaveNote state={refresh.state} />
      <QueuedJob data={queued} />
      <AddFamily ticker={ticker} onSaved={onChanged} />
    </section>
  );
}

function AddFamily({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PatentDraft>(emptyPatentDraft);
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const errors = validatePatent(draft);
  const set = <K extends keyof PatentDraft>(k: K, v: PatentDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof PatentDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    const ok = await run(
      () => getBrowserApiClient().POST("/holdings/{ticker}/patent-families", { params: { path: { ticker } }, body: patentRequest(draft) }),
      () => `Recorded family ${draft.familyKey.trim()}. It counts for ${ticker} only as far as its ownership basis allows.`,
      onSaved,
    );
    if (ok) {
      setDraft(emptyPatentDraft());
      setTouched(false);
    }
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
          Record a patent family
        </Button>
        <SaveNote state={state} />
      </div>
    );
  }
  return (
    <form onSubmit={submit} noValidate aria-label={`Record a patent family for ${ticker}`}>
      <FormFrame title="Record a patent family">
        <p className="max-w-[68ch] text-xs text-text-muted">
          Entered by you, shown apart from EPO data. Direct attribution needs ownership evidence; a subsidiary path needs the priority date.
        </p>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Family key" error={show("familyKey")}>
            <Input value={draft.familyKey} onChange={(e) => set("familyKey", e.target.value)} maxLength={160} />
          </Field>
          <Field label="Applicant" error={show("applicant")}>
            <Input value={draft.applicant} onChange={(e) => set("applicant", e.target.value)} maxLength={240} />
          </Field>
          <Field label="Title" error={show("title")} hint="Optional">
            <Input value={draft.title} onChange={(e) => set("title", e.target.value)} />
          </Field>
          <Field label="Priority date" error={show("priorityDate")} hint="YYYY-MM-DD">
            <Input value={draft.priorityDate} onChange={(e) => set("priorityDate", e.target.value)} />
          </Field>
          <Field label="Legal status">
            <select className={SELECT_CLASS} value={draft.legalStatus} onChange={(e) => set("legalStatus", e.target.value as PatentDraft["legalStatus"])}>
              {(["unknown", "application", "granted", "active", "expired"] as const).map((s) => (
                <option key={s} value={s}>
                  {s.charAt(0).toUpperCase() + s.slice(1)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="CPC codes" error={show("cpc")} hint="Separated by commas or spaces">
            <Input value={draft.cpc} onChange={(e) => set("cpc", e.target.value)} />
          </Field>
          <Field label="Jurisdictions" hint="For example EP, US">
            <Input value={draft.jurisdictions} onChange={(e) => set("jurisdictions", e.target.value)} />
          </Field>
          <Field label="Forward citations" error={show("fwd")} hint="Blank is recorded as 0 citations known">
            <Input inputMode="numeric" value={draft.fwd} onChange={(e) => set("fwd", e.target.value)} />
          </Field>
          <Field label="Backward citations" error={show("bwd")}>
            <Input inputMode="numeric" value={draft.bwd} onChange={(e) => set("bwd", e.target.value)} />
          </Field>
          <Field label="Source" error={show("source")}>
            <Input value={draft.source} onChange={(e) => set("source", e.target.value)} maxLength={80} />
          </Field>
          <Field label="Source link" error={show("sourceUrl")}>
            <Input value={draft.sourceUrl} onChange={(e) => set("sourceUrl", e.target.value)} placeholder="https://" />
          </Field>
          <Field label="Ownership confidence (0 to 1)" error={show("confidence")}>
            <Input inputMode="decimal" value={draft.confidence} onChange={(e) => set("confidence", e.target.value)} />
          </Field>
          <Field label="Ownership basis">
            <select className={SELECT_CLASS} value={draft.basis} onChange={(e) => set("basis", e.target.value as PatentDraft["basis"])}>
              {(Object.keys(BASIS_LABEL) as PatentDraft["basis"][]).map((b) => (
                <option key={b} value={b}>
                  {BASIS_LABEL[b]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Evidence id" error={show("evidenceId")} hint={draft.basis === "direct" ? "Required for direct" : "Optional"}>
            <Input inputMode="numeric" value={draft.evidenceId} onChange={(e) => set("evidenceId", e.target.value)} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={state.kind === "saving"}>
            Save family
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
            Close
          </Button>
          <OwnerTag />
          <SaveNote state={state} />
        </div>
      </FormFrame>
    </form>
  );
}
