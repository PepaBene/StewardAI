"use client";

import { useCallback, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";
import { apiErrorText } from "@/lib/reference/form-utils";
import {
  ACTION_TYPES,
  actionDecisionBody,
  actionRequest,
  emptyActionDraft,
  parseActions,
  safeUrl,
  validateAction,
  validateActionDecision,
  type ActionDecision,
  type ActionDraft,
  type CorporateAction,
} from "@/lib/relationships/relationships";

import { callApi, DecisionForm } from "./decision-form";
import { Fact, SourceLink, StatusMark } from "./status-mark";

function amountText(a: CorporateAction): string {
  if (a.cashAmount !== null) return `${a.cashAmount} ${a.currency ?? "(currency not recorded)"}`;
  if (a.ratio !== null) return `ratio ${a.ratio}`;
  return "not recorded";
}

const NEXT: Record<string, { value: ActionDecision; label: string }[]> = {
  suggested: [
    { value: "confirmed", label: "Confirm" },
    { value: "rejected", label: "Reject" },
  ],
  confirmed: [
    { value: "rejected", label: "Reject" },
    { value: "suggested", label: "Retract to review" },
  ],
  rejected: [
    { value: "confirmed", label: "Confirm" },
    { value: "suggested", label: "Retract to review" },
  ],
};

function ActionCard({ ticker, a, onChanged }: { ticker: string; a: CorporateAction; onChanged: () => void }) {
  const link = safeUrl(a.sourceUrl);
  return (
    <li className="space-y-2 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusMark status={a.status === "confirmed" || a.status === "rejected" ? a.status : "suggested"} />
        <span className="text-sm font-medium">{titleCase(a.actionType)}</span>
        <span className="num text-sm">{a.symbol}</span>
        <span className="num ml-auto text-xs text-text-muted">#{a.id}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-4">
        <Fact label="Ex-date">{a.exDate}</Fact>
        <Fact label="Amount">{amountText(a)}</Fact>
        <Fact label="Source">{a.source}</Fact>
        <Fact label="Linked evidence">{a.evidenceId === null ? "none" : `#${a.evidenceId}`}</Fact>
      </dl>
      <p className="text-xs text-text-muted">
        <SourceLink href={link} /> {a.decisionReason ? `· Last decision: ${a.decisionReason}` : ""}
      </p>
      <DecisionForm
        withEvidence
        options={NEXT[a.status ?? "suggested"] ?? NEXT.suggested}
        validate={(v, reason, ev) => validateActionDecision(v as ActionDecision, reason, ev, a.evidenceId)}
        onSubmit={async (v, reason, ev) => {
          const r = await callApi(
            () => getBrowserApiClient().POST("/holdings/{ticker}/corporate-actions/{action_id}/decision", { params: { path: { ticker, action_id: a.id } }, body: actionDecisionBody(v as ActionDecision, reason, ev) }),
            `Saved: action ${a.id} is now ${v}.`,
          );
          if (r.ok) onChanged();
          return r;
        }}
      />
    </li>
  );
}

/** Record an action by hand (POST /holdings/{ticker}/corporate-actions); the form mirrors the API model. */
function ActionEntry({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [draft, setDraft] = useState<ActionDraft>(emptyActionDraft());
  const [touched, setTouched] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validateAction(draft);
  const set = <K extends keyof ActionDraft>(k: K, v: ActionDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof ActionDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().POST("/holdings/{ticker}/corporate-actions", { params: { path: { ticker } }, body: actionRequest(draft) });
      if (r.response.ok) {
        setSave({ kind: "ok", text: `Saved a ${titleCase(draft.actionType).toLowerCase()} for ${draft.symbol.trim()}. It is recorded as ${draft.status}.` });
        setDraft(emptyActionDraft(draft.symbol));
        setTouched(false);
        onSaved();
      } else setSave({ kind: "error", text: apiErrorText(r.error, r.response.status) });
    } catch (err) {
      setSave({ kind: "error", text: `Not saved: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  return (
    <details className="border-t border-border-soft pt-2">
      <summary className="cursor-pointer text-sm text-accent-ink hover:underline">Record an action by hand</summary>
      <form onSubmit={submit} noValidate aria-label="Record a corporate action" className="mt-2">
        <FormFrame title={`Record a corporate action for ${ticker}`}>
          <p className="max-w-[68ch] text-xs text-text-muted">
            <OwnerTag>Entered by you</OwnerTag> The symbol must be this holding&apos;s listing ticker or a provider symbol, and the holding needs a confirmed listing match. Saving the same action again updates it; an already decided action keeps its status.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Type">
              <select value={draft.actionType} onChange={(e) => set("actionType", e.target.value as ActionDraft["actionType"])} className={SELECT_CLASS}>
                {ACTION_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {titleCase(t)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Symbol" error={show("symbol")}>
              <Input value={draft.symbol} onChange={(e) => set("symbol", e.target.value)} maxLength={40} className="num" />
            </Field>
            <Field label="Ex-date" hint="YYYY-MM-DD." error={show("exDate")}>
              <Input value={draft.exDate} onChange={(e) => set("exDate", e.target.value)} placeholder="2026-03-01" maxLength={10} className="num" />
            </Field>
            <Field label="Record date" hint="Optional." error={show("recordDate")}>
              <Input value={draft.recordDate} onChange={(e) => set("recordDate", e.target.value)} maxLength={10} className="num" />
            </Field>
            <Field label="Payment date" hint="Optional." error={show("paymentDate")}>
              <Input value={draft.paymentDate} onChange={(e) => set("paymentDate", e.target.value)} maxLength={10} className="num" />
            </Field>
            <Field label="Ratio" hint={draft.actionType === "split" || draft.actionType === "stock_dividend" ? "Required, above 0." : "Optional, above 0."} error={show("ratio")}>
              <Input value={draft.ratio} onChange={(e) => set("ratio", e.target.value)} inputMode="decimal" className="num" />
            </Field>
            <Field label="Cash amount" hint={draft.actionType === "cash_dividend" ? "Required, 0 or more." : "Optional, 0 or more."} error={show("cashAmount")}>
              <Input value={draft.cashAmount} onChange={(e) => set("cashAmount", e.target.value)} inputMode="decimal" className="num" />
            </Field>
            <Field label="Currency" hint="USD, EUR, or GBp, ZAc, ILa for minor units." error={show("currency")}>
              <Input value={draft.currency} onChange={(e) => set("currency", e.target.value)} maxLength={3} className="num" />
            </Field>
            <Field label="Amount is per" hint="Ordinary share only when the holding is an ADS.">
              <select value={draft.per} onChange={(e) => set("per", e.target.value as ActionDraft["per"])} className={SELECT_CLASS}>
                <option value="listed_line">The listed line</option>
                <option value="ordinary_share">Ordinary share</option>
              </select>
            </Field>
            <Field label="Amount basis">
              <select value={draft.amountBasis} onChange={(e) => set("amountBasis", e.target.value as ActionDraft["amountBasis"])} className={SELECT_CLASS}>
                <option value="as_declared">As declared</option>
                <option value="split_adjusted">Restated for later splits</option>
              </select>
            </Field>
            <Field label="Source name" hint="2 to 80 characters." error={show("source")}>
              <Input value={draft.source} onChange={(e) => set("source", e.target.value)} maxLength={80} />
            </Field>
            <Field label="Source address" hint="Full web address." error={show("sourceUrl")}>
              <Input type="url" value={draft.sourceUrl} onChange={(e) => set("sourceUrl", e.target.value)} placeholder="https://" maxLength={2000} />
            </Field>
            <Field label="Evidence number" hint="Required to record it as confirmed." error={show("evidenceId")}>
              <Input value={draft.evidenceId} onChange={(e) => set("evidenceId", e.target.value)} inputMode="numeric" className="num" />
            </Field>
            <Field label="Record it as">
              <select value={draft.status} onChange={(e) => set("status", e.target.value as ActionDraft["status"])} className={SELECT_CLASS}>
                <option value="suggested">Suggested, to decide later</option>
                <option value="confirmed">Confirmed</option>
                <option value="rejected">Rejected</option>
              </select>
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" disabled={save.kind === "saving"}>
              Save action
            </Button>
            <SaveNote state={save} />
          </div>
        </FormFrame>
      </form>
    </details>
  );
}

/** Section 5: recorded corporate actions of one holding awaiting confirmation or rejection. */
export function ActionsPanel({ ticker }: { ticker: string | null }) {
  return (
    <Panel id="actions" title="Corporate-action proposals" description="Splits, dividends and other actions recorded for a holding. Only a confirmed action adjusts prices and returns; a confirmed action needs linked source evidence.">
      {ticker === null ? <EmptyState title="Choose a holding">Enter a ticker above to list its corporate actions.</EmptyState> : <ActionsFor key={ticker} ticker={ticker} />}
    </Panel>
  );
}

function ActionsFor({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/corporate-actions", { params: { path: { ticker } } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  const data = dataOf(state);
  const rows = data ? parseActions(data) : null;
  return (
    <div className="space-y-3">
      <ResourceNotice state={state} what={`corporate actions for ${ticker}`} />
      {rows && rows.length === 0 ? <EmptyState title={`No corporate actions for ${ticker}`}>Actions appear when a provider reports one, or when you record one below.</EmptyState> : null}
      {rows && rows.length > 0 ? (
        <ul className="space-y-3">
          {rows.map((a) => (
            <ActionCard key={a.id} ticker={ticker} a={a} onChanged={reload} />
          ))}
        </ul>
      ) : null}
      <ActionEntry ticker={ticker} onSaved={reload} />
    </div>
  );
}
