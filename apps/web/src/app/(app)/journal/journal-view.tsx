"use client";

import { useRef, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText, SELECT, TEXTAREA } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  buildEntry,
  DECISIONS,
  daysUntil,
  decisionLabel,
  EMPTY_FORM,
  evidenceLines,
  reasonLabel,
  REASONS,
  reviewLabel,
  type EntryForm,
  type JournalEntry,
} from "@/lib/journal/journal";
import { cn } from "@/lib/utils";

const fetchEntries = () => getBrowserApiClient().GET("/journal", { params: { query: {} } });

export function JournalView() {
  const { state, reload } = useApiResource(fetchEntries);
  const formRef = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState<EntryForm>(EMPTY_FORM);
  const [filter, setFilter] = useState("");

  function review(entry: JournalEntry) {
    setForm({
      ...EMPTY_FORM,
      scope: entry.scope,
      ticker: entry.ticker ?? "",
      decision: "review",
      parent_id: String(entry.id),
    });
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="flex flex-col gap-5">
      <div ref={formRef}>
        <EntryFormPanel form={form} setForm={setForm} onSaved={reload} />
      </div>
      <ResourceGate state={state} label="the journal">
        {(entries) => <Ledger entries={entries} filter={filter} setFilter={setFilter} onReview={review} />}
      </ResourceGate>
    </div>
  );
}

function EntryFormPanel({ form, setForm, onSaved }: { form: EntryForm; setForm: (f: EntryForm) => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const set = <K extends keyof EntryForm>(key: K, value: EntryForm[K]) => setForm({ ...form, [key]: value });
  const reviewing = form.parent_id !== "";

  async function save() {
    const built = buildEntry(form);
    if (!built.ok) return setProblem(built.error);
    setBusy(true);
    setProblem(null);
    setDone(false);
    const res = await getBrowserApiClient().POST("/journal", { body: built.body });
    setBusy(false);
    if (!res.response.ok) return setProblem(problemText(res.error, "The entry could not be saved."));
    setForm(EMPTY_FORM);
    setDone(true);
    onSaved();
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={2}
        kicker={reviewing ? `Reviewing entry ${form.parent_id}` : "New entry"}
        title={reviewing ? "What happened since, and what now" : "Record a decision"}
        action={
          reviewing ? (
            <Button type="button" variant="ghost" size="sm" onClick={() => setForm(EMPTY_FORM)}>
              Start a new entry instead
            </Button>
          ) : null
        }
      />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        The thesis, your holding, the price and the calculation versions in force are stored with the entry, so you can judge the outcome later against what you knew.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Field label="About">
          <select className={SELECT} value={form.scope} onChange={(e) => set("scope", e.target.value as EntryForm["scope"])} disabled={reviewing}>
            <option value="security">A security</option>
            <option value="portfolio">The whole portfolio</option>
          </select>
        </Field>
        <Field label="Ticker" hint="As in your holdings">
          <Input value={form.ticker} disabled={form.scope === "portfolio" || reviewing} onChange={(e) => set("ticker", e.target.value.toUpperCase())} />
        </Field>
        <Field label="Decision">
          <select className={SELECT} value={form.decision} onChange={(e) => set("decision", e.target.value as EntryForm["decision"])}>
            {DECISIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Price" hint="Blank uses the latest stored price">
          <Input inputMode="decimal" value={form.price} onChange={(e) => set("price", e.target.value)} />
        </Field>
      </div>
      <p className="mt-1 text-xs text-text-muted">{DECISIONS.find((d) => d.value === form.decision)?.help}</p>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <Field label="What you believe, and why">
          <textarea className={TEXTAREA} value={form.hypothesis} onChange={(e) => set("hypothesis", e.target.value)} maxLength={4000} />
        </Field>
        <Field label="Valuation assumptions" hint="Growth, margins, multiple, discount rate">
          <textarea className={TEXTAREA} value={form.valuation_assumptions} onChange={(e) => set("valuation_assumptions", e.target.value)} maxLength={4000} />
        </Field>
        <Field label="Expected catalysts" hint="One per line">
          <textarea className={TEXTAREA} value={form.catalysts} onChange={(e) => set("catalysts", e.target.value)} />
        </Field>
        <Field label="What would prove you wrong" hint="One per line">
          <textarea className={TEXTAREA} value={form.disconfirming} onChange={(e) => set("disconfirming", e.target.value)} />
        </Field>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <Field label="Review by">
          <Input type="date" value={form.review_date} onChange={(e) => set("review_date", e.target.value)} />
        </Field>
        <Field label="Conviction">
          <select className={SELECT} value={form.conviction} onChange={(e) => set("conviction", e.target.value)}>
            <option value="">Not stated</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n} of 5
              </option>
            ))}
          </select>
        </Field>
        <Field label="Main reason">
          <select className={SELECT} value={form.main_reason} onChange={(e) => set("main_reason", e.target.value)}>
            <option value="">Not stated</option>
            {REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save entry"}
        </Button>
        {done ? <span className="text-sm text-text-muted">Saved. It cannot be edited; review it later instead.</span> : null}
      </div>
      <div className="mt-3">
        <Problem>{problem}</Problem>
      </div>
    </Blueprint>
  );
}

function Ledger({ entries, filter, setFilter, onReview }: { entries: JournalEntry[]; filter: string; setFilter: (v: string) => void; onReview: (e: JournalEntry) => void }) {
  const shown = entries.filter((e) => filter.trim() === "" || (e.ticker ?? "portfolio").toLowerCase().includes(filter.trim().toLowerCase()));
  const reviewedIds = new Set(entries.map((e) => e.parent_id).filter((id): id is number => id !== null));
  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={2}
        kicker="Ledger"
        title={entries.length === 0 ? "No decisions recorded yet" : `${entries.length} decision${entries.length === 1 ? "" : "s"}`}
        action={
          entries.length > 0 ? (
            <Field label="Filter by ticker" className="w-40">
              <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="All" />
            </Field>
          ) : null
        }
      />
      {entries.length === 0 ? (
        <p className="mt-2 max-w-prose text-sm text-text-muted">Record the next buy, sale or pass above. The scorecard needs at least five decisions of a kind before it reports a figure.</p>
      ) : shown.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">Nothing matches that ticker.</p>
      ) : (
        <ol className="mt-3 divide-y divide-border-soft border-y border-border-soft">
          {shown.map((entry) => (
            <Row key={entry.id} entry={entry} reviewed={reviewedIds.has(entry.id)} onReview={() => onReview(entry)} />
          ))}
        </ol>
      )}
    </Blueprint>
  );
}

function Row({ entry, reviewed, onReview }: { entry: JournalEntry; reviewed: boolean; onReview: () => void }) {
  const days = reviewed || entry.decision === "review" ? null : daysUntil(entry.review_date, new Date());
  const due = reviewLabel(days);
  return (
    <li className="grid gap-x-4 gap-y-1 py-3 sm:grid-cols-[7rem_1fr]">
      <div className="flex items-baseline gap-2 sm:flex-col sm:gap-0.5">
        <span className="num text-xs text-text-muted">{formatDate(entry.decided_on)}</span>
        <span className="text-sm font-semibold">{decisionLabel(entry.decision)}</span>
      </div>
      <div className="min-w-0 space-y-1.5">
        <p className="text-sm">
          <span className="font-semibold">{entry.ticker ?? "Portfolio"}</span>
          {entry.parent_id ? <span className="text-text-muted"> · reviews entry {entry.parent_id}</span> : null}
          {entry.conviction ? <span className="text-text-muted"> · conviction {entry.conviction} of 5</span> : null}
          {entry.main_reason ? <span className="text-text-muted"> · {reasonLabel(entry.main_reason).toLowerCase()}</span> : null}
          {entry.price_at_decision ? (
            <span className="num text-text-muted">
              {" "}
              · at {entry.price_at_decision}
              {entry.price_currency ? ` ${entry.price_currency}` : ""}
            </span>
          ) : null}
        </p>
        <p className="max-w-prose text-sm whitespace-pre-line">{entry.hypothesis}</p>
        {due ? <p className={cn("text-xs", days !== null && days < 0 ? "font-semibold text-warning-signal" : "text-text-muted")}>{due}</p> : null}
        <details className="text-sm">
          <summary className="cursor-pointer text-text-muted">What it rested on</summary>
          <div className="mt-2 space-y-2">
            {entry.valuation_assumptions ? <Block title="Valuation assumptions">{entry.valuation_assumptions}</Block> : null}
            <List title="Expected catalysts" items={entry.catalysts} />
            <List title="What would prove it wrong" items={entry.disconfirming} />
            <List title="Evidence at the time" items={evidenceLines(entry.snapshot, entry.scope === "portfolio")} />
          </div>
        </details>
        <div>
          <Button type="button" size="sm" variant="ghost" onClick={onReview}>
            Review this
          </Button>
        </div>
      </div>
    </li>
  );
}

function Block({ title, children }: { title: string; children: string }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{title}</div>
      <p className="max-w-prose whitespace-pre-line">{children}</p>
    </div>
  );
}

function List({ title, items }: { title: string; items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{title}</div>
      <ul className="list-disc pl-5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
