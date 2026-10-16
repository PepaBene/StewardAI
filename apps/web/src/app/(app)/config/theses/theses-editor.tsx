"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { SaveBar } from "@/components/config/save-bar";
import {
  SelectField,
  TextAreaField,
  TextField,
} from "@/components/config/fields";
import { MoreSettings, ShareBar, SummaryPanel } from "@/components/config/summary";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type Holding,
  type ThesesConfig,
} from "@/lib/api/client";
import { errorsAt, type FieldErrors } from "@/lib/api/validation-errors";
import {
  BASES,
  KNOWN_METRICS,
  KPI_PERIODS,
  MAIN_REASONS,
  newKpi,
  newThesis,
  OPERATORS,
  preMortemAsExits,
  rigorGaps,
  slugFromMetric,
  thesesFromBody,
  thesesToBody,
  UNIT_EXAMPLES,
  type KpiForm,
  type RigorForm,
  type TargetForm,
  type ThesisForm,
} from "@/lib/config/theses-form";
import {
  configSaveQuery,
  useConfigEditor,
} from "@/lib/hooks/use-config-editor";

const api = () => getBrowserApiClient();

type ThesisStatus = "complete" | "gaps" | "overdue";
const STATUS_LABEL: Record<ThesisStatus, string> = {
  complete: "Complete",
  gaps: "Gaps open",
  overdue: "Review overdue",
};

/** Overdue review first, then missing rigor, otherwise complete. */
function thesisStatus(f: ThesisForm, today: string): ThesisStatus {
  if (f.review_date && f.review_date < today) return "overdue";
  return rigorGaps(f.rigor).length > 0 ? "gaps" : "complete";
}

export function ThesesEditor() {
  const [forms, setForms] = useState<ThesisForm[]>([]);
  const [baseline, setBaseline] = useState<string>("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [verdictJob, setVerdictJob] = useState<string | null>(null);

  const editor = useConfigEditor<ThesesConfig>({
    load: () => api().GET("/config/theses"),
    save: (body, note, base) =>
      api().PUT("/config/theses", {
        body,
        params: { query: configSaveQuery(note, base) },
      }),
    validate: (body) => api().POST("/config/theses/validate", { body }),
    onLoaded: (body) => {
      const loaded = thesesFromBody(body);
      setForms(loaded);
      setBaseline(JSON.stringify(thesesToBody(loaded)));
    },
  });

  useEffect(() => {
    let cancelled = false;
    api()
      .GET("/portfolio/holdings")
      .then((r) => {
        if (!cancelled && r.data) setHoldings(r.data.holdings);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  // The thesis named in the URL hash (links from a holding page) opens first, until another is picked.
  const [hashTicker] = useState(() => (typeof window === "undefined" ? "" : decodeURIComponent(window.location.hash.slice(1))));

  const body = useMemo(() => thesesToBody(forms), [forms]);
  const dirty = JSON.stringify(body) !== baseline;
  const withoutThesis = holdings.filter(
    (h) => !forms.some((f) => f.ticker === h.ticker),
  );
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const current = forms.find((f) => f.key === selectedKey) ?? (hashTicker ? forms.find((f) => f.ticker === hashTicker) : undefined) ?? forms[0];
  const statusCounts = { complete: 0, gaps: 0, overdue: 0 };
  for (const f of forms) statusCounts[thesisStatus(f, today)] += 1;
  const nameOf = (ticker: string) => {
    const h = holdings.find((x) => x.ticker === ticker);
    return h?.name ?? (ticker || "New thesis");
  };
  const addThesis = (ticker = "") => {
    const t = newThesis(ticker);
    setForms((all) => [...all, t]);
    setSelectedKey(t.key);
  };

  const update = (key: string, change: (form: ThesisForm) => ThesisForm) =>
    setForms((all) => all.map((f) => (f.key === key ? change(f) : f)));

  async function save(note: string | null) {
    setVerdictJob(null);
    const saved = await editor.save(body, note);
    if (!saved) return;
    setBaseline(JSON.stringify(body));
    // The next verdict run uses the new thresholds: queue it now instead of waiting for a release.
    const job = await api()
      .POST("/jobs/thesis-verdicts", { body: {} })
      .catch(() => null);
    setVerdictJob(
      job?.data
        ? `Verdicts are being re-judged with version #${saved.id} (job #${job.data.id}).`
        : "Saved, but re-running the verdicts could not be queued; use Re-run verdicts on a holding page.",
    );
  }

  return (
    <div className="space-y-6">
      <ConfigHeader title="Theses" domain="theses">
        <p>
          Why you own each holding, and the KPIs that must hold. Thresholds are
          absolute: the earnings check extracts the figures from the
          company&rsquo;s release and judges each KPI pass, breach or unknown.
          Saving creates a new version; earlier verdicts stay tied to the
          version they were judged under.
        </p>
      </ConfigHeader>

      {editor.status.kind === "loading" ? (
        <p
          className="flex items-center gap-2 text-sm text-text-muted"
          role="status"
        >
          <LoaderCircleIcon
            aria-hidden="true"
            className="size-4 animate-spin"
          />
          Loading theses…
        </p>
      ) : null}
      {editor.status.kind === "error" ? (
        <AlertMessage title="Could not load the theses">
          {editor.status.message}
        </AlertMessage>
      ) : null}

      {editor.status.kind === "ready" ? (
        <>
          {forms.length === 0 ? (
            <EmptyState title="No theses yet">
              Add one for each holding you want monitored.
            </EmptyState>
          ) : null}

          {forms.length > 0 ? (
            <SummaryPanel
              title="Thesis status"
              reading={`${forms.length} thesis${forms.length === 1 ? "" : "es"}; ${withoutThesis.length} holding${withoutThesis.length === 1 ? " has" : "s have"} none.`}
            >
              <ShareBar
                label="Theses by status"
                segments={[
                  { label: STATUS_LABEL.complete, value: statusCounts.complete, tone: "gain" },
                  { label: STATUS_LABEL.gaps, value: statusCounts.gaps, tone: "warning" },
                  { label: STATUS_LABEL.overdue, value: statusCounts.overdue, tone: "loss" },
                ]}
              />
            </SummaryPanel>
          ) : null}

          {forms.length > 0 ? (
            <div className="grid items-start gap-4 lg:grid-cols-[20rem_1fr]">
              <ul aria-label="Theses" className="space-y-1">
                {forms.map((f) => (
                  <li key={f.key}>
                    <button
                      type="button"
                      aria-current={f.key === current?.key ? "true" : undefined}
                      onClick={() => setSelectedKey(f.key)}
                      className={`block w-full rounded-control px-3 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-focus/30 ${
                        f.key === current?.key ? "bg-surface-muted font-semibold" : "hover:bg-surface-muted"
                      }`}
                    >
                      <span className="block truncate">{nameOf(f.ticker)}</span>
                      <span className="block truncate text-xs font-normal text-text-muted">
                        {STATUS_LABEL[thesisStatus(f, today)]}
                        {f.review_date ? `, review ${f.review_date}` : ", no review date"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {current ? (
                <ThesisCard
                  key={current.key}
                  form={current}
                  index={forms.findIndex((f) => f.key === current.key)}
                  errors={editor.errors}
                  onChange={(change) => update(current.key, change)}
                  onRemove={() =>
                    setForms((all) => all.filter((f) => f.key !== current.key))
                  }
                />
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => addThesis()}
            >
              <PlusIcon aria-hidden="true" />
              Add thesis
            </Button>
            {withoutThesis.slice(0, 6).map((h) => (
              <Button
                key={h.ticker}
                variant="ghost"
                size="sm"
                onClick={() => addThesis(h.ticker)}
              >
                <PlusIcon aria-hidden="true" />
                {h.name ?? h.ticker}
              </Button>
            ))}
          </div>
          <datalist id="holding-tickers">
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name}
              </option>
            ))}
          </datalist>
          <datalist id="kpi-metrics">
            {KNOWN_METRICS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <datalist id="kpi-units">
            {UNIT_EXAMPLES.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>

          {verdictJob ? (
            <AlertMessage tone="info" title="Saved">
              {verdictJob}
            </AlertMessage>
          ) : null}
          <SaveBar
            dirty={dirty}
            savedVersionId={editor.version?.id ?? null}
            busy={editor.busy}
            errors={editor.errors}
            message={editor.message}
            onValidate={() => void editor.validate(body)}
            onSave={(note) => void save(note)}
          />
        </>
      ) : null}
    </div>
  );
}

function ThesisCard({
  form,
  index,
  errors,
  onChange,
  onRemove,
}: {
  form: ThesisForm;
  index: number;
  errors: FieldErrors;
  onChange: (change: (form: ThesisForm) => ThesisForm) => void;
  onRemove: () => void;
}) {
  const path = `theses.${index}`;
  const set = <K extends keyof ThesisForm>(key: K, value: ThesisForm[K]) =>
    onChange((f) => ({ ...f, [key]: value }));
  const setKpi = (key: string, change: Partial<KpiForm>) =>
    onChange((f) => ({
      ...f,
      kpis: f.kpis.map((k) => (k.key === key ? { ...k, ...change } : k)),
    }));
  const setTarget = (change: Partial<TargetForm>) =>
    onChange((f) => ({ ...f, target: { ...f.target, ...change } }));
  const setRigor = (change: Partial<RigorForm>) =>
    onChange((f) => ({ ...f, rigor: { ...f.rigor, ...change } }));
  const gaps = rigorGaps(form.rigor);
  // Fixed at mount (the card is keyed per thesis) so typing never collapses a disclosure.
  const [openScenarios] = useState(() => Boolean(form.target.bear || form.target.base || form.target.bull));
  const [openRigor] = useState(() => gaps.length > 0 && gaps.length < 6);
  const [prefillBusy, setPrefillBusy] = useState(false);
  const [prefillNote, setPrefillNote] = useState<string | null>(null);

  /** Fills "growth the price implies" from the valuation tab's reverse DCF. Nothing is saved until you save the thesis. */
  async function fillImplied() {
    setPrefillBusy(true);
    setPrefillNote(null);
    const res = await api().GET("/companies/{ticker}/thesis-prefill", {
      params: { path: { ticker: form.ticker.split("_")[0] } },
    });
    setPrefillBusy(false);
    if (!res.response.ok || !res.data) return setPrefillNote("The valuation could not be read for this ticker.");
    if (res.data.implied_growth === null || res.data.implied_growth === undefined) {
      return setPrefillNote(res.data.reason ?? "The valuation gives no figure.");
    }
    setRigor({ implied_growth: (res.data.implied_growth * 100).toFixed(1) });
    setPrefillNote("Filled from the base-case reverse DCF. Check it before you save.");
  }

  const [draftBusy, setDraftBusy] = useState(false);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const [draftFacts, setDraftFacts] = useState<string[]>([]);

  /** Drafts the text fields from the stored research and figures. It fills only fields that are empty and saves nothing. */
  async function draftText() {
    setDraftBusy(true);
    setDraftNote(null);
    setDraftFacts([]);
    const res = await api().POST("/companies/{ticker}/thesis-draft", {
      params: { path: { ticker: form.ticker.split("_")[0] } },
    });
    setDraftBusy(false);
    if (!res.response.ok || !res.data) {
      const detail = (res.error as { detail?: unknown } | undefined)?.detail;
      return setDraftNote(typeof detail === "string" ? detail : "The draft could not be made.");
    }
    const d = res.data;
    const lines = (items: { text: string }[]) => items.map((i) => i.text).join("\n");
    onChange((f) => {
      const next = { ...f, rigor: { ...f.rigor } };
      if (!f.why_owned.trim() && d.why_owned) {
        next.why_owned = d.why_owned.text;
      }
      if (!f.exit_conditions.trim() && d.exit_conditions.length > 0) {
        next.exit_conditions = lines(d.exit_conditions);
      }
      if (!f.rigor.pre_mortem.trim() && d.pre_mortem.length > 0) {
        next.rigor.pre_mortem = lines(d.pre_mortem);
      }
      if (!f.rigor.belief.trim() && !f.rigor.disproof.trim() && d.variant_perception) {
        next.rigor.belief = d.variant_perception.belief;
        next.rigor.disproof = d.variant_perception.disproof;
      }
      return next;
    });
    const facts = [
      d.why_owned,
      ...d.exit_conditions,
      ...d.pre_mortem,
      d.variant_perception,
    ].flatMap((x) => (x ? x.facts : []));
    setDraftFacts([...new Set(facts)]);
    setDraftNote(
      `Draft written into the empty fields. Check every line against the facts below, then edit and save. ${
        d.dropped > 0 ? `${d.dropped} line(s) with no supporting fact were removed. ` : ""
      }Left for you: ${d.left_to_owner.join("; ")}. ${d.notes.join(" ")}`,
    );
  }

  return (
    <section
      id={form.ticker || undefined}
      aria-label={`Thesis ${form.ticker || index + 1}`}
      className="scroll-mt-24 space-y-4 rounded-card bg-surface-muted p-5"
    >
      <div className="flex flex-wrap items-start gap-3">
        <TextField
          className="w-56"
          label="Ticker"
          path={`${path}.ticker`}
          errors={errors}
          value={form.ticker}
          onChange={(v) => set("ticker", v)}
          list="holding-tickers"
          placeholder="AAPL_US_EQ"
          hint="Trading212 ticker"
        />
        <TextField
          className="w-44"
          label="Review date"
          type="date"
          path={`${path}.review_date`}
          errors={errors}
          value={form.review_date}
          onChange={(v) => set("review_date", v)}
        />
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={onRemove}
        >
          <Trash2Icon aria-hidden="true" />
          Remove thesis
        </Button>
      </div>
      <div className="flex flex-col gap-1">
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!form.ticker || draftBusy}
            onClick={() => void draftText()}
          >
            {draftBusy ? "Drafting…" : "Draft from research"}
          </Button>
        </div>
        {draftNote ? <p className="max-w-prose text-xs text-text-muted">{draftNote}</p> : null}
        {draftFacts.length > 0 ? (
          <details className="max-w-prose text-xs">
            <summary className="cursor-pointer">Facts the draft rests on ({draftFacts.length})</summary>
            <ul className="mt-1 list-disc pl-5">
              {draftFacts.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
      <TextAreaField
        label="Why it is owned"
        path={`${path}.why_owned`}
        errors={errors}
        value={form.why_owned}
        onChange={(v) => set("why_owned", v)}
      />
      <TextAreaField
        label="Exit conditions"
        path={`${path}.exit_conditions`}
        errors={errors}
        value={form.exit_conditions}
        onChange={(v) => set("exit_conditions", v)}
        hint="One per line."
        rows={2}
      />

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Price target</legend>
        <p className="text-sm text-text-muted">
          Checked after each sync. At the sell target, re-run the research: sell
          unless the new thesis raises the target. At or below the buy target,
          the holding is a candidate to add to. Scenarios alone are enough. Leave all blank for none.
        </p>
        <div className="grid grid-cols-2 items-start gap-2 sm:grid-cols-3 lg:grid-cols-[0.8fr_0.8fr_0.8fr_0.6fr_1.6fr_0.9fr]">
          <TextField
            label="Sell target"
            path={`${path}.price_target.sell_target`}
            errors={errors}
            value={form.target.sell_target}
            inputMode="decimal"
            className="num"
            onChange={(v) => setTarget({ sell_target: v })}
          />
          <TextField
            label="Buy target"
            path={`${path}.price_target.buy_target`}
            errors={errors}
            value={form.target.buy_target}
            inputMode="decimal"
            className="num"
            onChange={(v) => setTarget({ buy_target: v })}
          />
          <TextField
            label="Bear-case price"
            path={`${path}.price_target.bear_case`}
            errors={errors}
            value={form.target.bear_case}
            inputMode="decimal"
            className="num"
            hint="If the thesis fails"
            onChange={(v) => setTarget({ bear_case: v })}
          />
          <TextField
            label="Currency"
            path={`${path}.price_target.currency`}
            errors={errors}
            value={form.target.currency}
            placeholder="USD"
            hint="As Trading212 quotes it (GBX for pence)"
            onChange={(v) => setTarget({ currency: v })}
          />
          <TextField
            label="Valuation method"
            path={`${path}.price_target.method`}
            errors={errors}
            value={form.target.method}
            placeholder="DCF, 10% discount rate"
            onChange={(v) => setTarget({ method: v })}
          />
          <TextField
            label="Set on"
            type="date"
            path={`${path}.price_target.set_on`}
            errors={errors}
            value={form.target.set_on}
            onChange={(v) => setTarget({ set_on: v })}
          />
        </div>
        <MoreSettings
          title="Scenarios"
          hint="Bear, base and bull values with their chances"
          defaultOpen={openScenarios}
          className="bg-surface"
        >
          <p className="text-sm text-text-muted">
            What one share is worth if the thesis fails, plays out, or plays out better, in the
            currency above, with how likely you think each is. They give the expected return and
            rank this stock against the others. Leave the probabilities blank for 25 / 50 / 25.
          </p>
          <div className="grid grid-cols-2 items-start gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <TextField label="Bear value" path={`${path}.price_target.scenarios.bear`} errors={errors} value={form.target.bear} inputMode="decimal" className="num" onChange={(v) => setTarget({ bear: v })} />
            <TextField label="Base value" path={`${path}.price_target.scenarios.base`} errors={errors} value={form.target.base} inputMode="decimal" className="num" onChange={(v) => setTarget({ base: v })} />
            <TextField label="Bull value" path={`${path}.price_target.scenarios.bull`} errors={errors} value={form.target.bull} inputMode="decimal" className="num" onChange={(v) => setTarget({ bull: v })} />
            <TextField label="Bear chance (%)" path={`${path}.price_target.scenarios.p_bear`} errors={errors} value={form.target.p_bear} inputMode="decimal" className="num" placeholder="25" onChange={(v) => setTarget({ p_bear: v })} />
            <TextField label="Base chance (%)" path={`${path}.price_target.scenarios.p_base`} errors={errors} value={form.target.p_base} inputMode="decimal" className="num" placeholder="50" onChange={(v) => setTarget({ p_base: v })} />
            <TextField label="Bull chance (%)" path={`${path}.price_target.scenarios.p_bull`} errors={errors} value={form.target.p_bull} inputMode="decimal" className="num" placeholder="25" onChange={(v) => setTarget({ p_bull: v })} />
          </div>
          <div className="grid grid-cols-2 items-start gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <TextField label="Catalyst date" type="date" path={`${path}.price_target.catalyst_date`} errors={errors} value={form.target.catalyst_date} hint="Blank: two years" onChange={(v) => setTarget({ catalyst_date: v })} />
            <TextField label="Expected volatility (%)" path={`${path}.price_target.volatility_band`} errors={errors} value={form.target.volatility_band} inputMode="decimal" className="num" hint="A year, e.g. 30" onChange={(v) => setTarget({ volatility_band: v })} />
          </div>
        </MoreSettings>
        {errorsAt(errors, `${path}.price_target`).map((m) => (
          <p key={m} className="text-xs text-text">
            <span aria-hidden="true" className="text-warning-signal">
              ●{" "}
            </span>
            {m}
          </p>
        ))}
      </fieldset>

      <MoreSettings
        title="Before you rely on it"
        hint={gaps.length > 0 ? `${gaps.length} still missing` : "Every requirement is filled in"}
        defaultOpen={openRigor}
        className="bg-surface"
      >
      <fieldset className="space-y-3">
        <legend className="sr-only">Before you rely on it</legend>
        <p className="text-sm text-text-muted">
          What the price already assumes, how often that has happened, how it
          could fail and what you know that the market does not.
          {gaps.length > 0
            ? ` Still missing: ${gaps.join("; ")}.`
            : " Every requirement is filled in."}
        </p>
        <div className="grid grid-cols-2 items-start gap-2 sm:grid-cols-4">
          <TextField
            label="Growth the price implies"
            path={`${path}.implied_growth`}
            errors={errors}
            value={form.rigor.implied_growth}
            inputMode="decimal"
            className="num"
            hint="% a year, from the reverse DCF"
            onChange={(v) => setRigor({ implied_growth: v })}
          />
          <div className="col-span-2 flex flex-col justify-end gap-1 pb-1 sm:col-span-1">
            <Button
              type="button"
              variant="outline"
              disabled={!form.ticker || prefillBusy}
              onClick={() => void fillImplied()}
            >
              {prefillBusy ? "Reading…" : "Fill from valuation"}
            </Button>
            {prefillNote ? <p className="text-xs text-text-muted">{prefillNote}</p> : null}
          </div>
          <TextField
            label="Companies that achieved it"
            path={`${path}.base_rate.share_achieved`}
            errors={errors}
            value={form.rigor.base_share}
            inputMode="decimal"
            className="num"
            hint="% of comparable companies"
            onChange={(v) => setRigor({ base_share: v })}
          />
          <TextField
            className="col-span-2"
            label="Which companies were compared"
            path={`${path}.base_rate.comparison`}
            errors={errors}
            value={form.rigor.base_comparison}
            placeholder="US software, 5-10 bn revenue, 10-year growth"
            onChange={(v) => setRigor({ base_comparison: v })}
          />
          <TextField
            className="col-span-2 sm:col-span-4"
            label="Source of the base rate"
            path={`${path}.base_rate.source`}
            errors={errors}
            value={form.rigor.base_source}
            onChange={(v) => setRigor({ base_source: v })}
          />
        </div>
        <TextAreaField
          label="Pre-mortem: the three likeliest reasons it loses 40%"
          path={`${path}.pre_mortem`}
          errors={errors}
          value={form.rigor.pre_mortem}
          onChange={(v) => setRigor({ pre_mortem: v })}
          hint="One per line, at most three."
          rows={3}
        />
        <div>
          <Button
            variant="outline"
            size="sm"
            disabled={form.rigor.pre_mortem.trim() === ""}
            onClick={() =>
              set(
                "exit_conditions",
                preMortemAsExits(form.exit_conditions, form.rigor.pre_mortem),
              )
            }
          >
            Add these as exit conditions
          </Button>
        </div>
        <div className="grid gap-2 lg:grid-cols-2">
          <TextAreaField
            label="What you believe that the market does not"
            path={`${path}.variant_perception.belief`}
            errors={errors}
            value={form.rigor.belief}
            onChange={(v) => setRigor({ belief: v })}
            rows={2}
          />
          <TextAreaField
            label="The evidence that would prove you wrong"
            path={`${path}.variant_perception.disproof`}
            errors={errors}
            value={form.rigor.disproof}
            onChange={(v) => setRigor({ disproof: v })}
            rows={2}
          />
        </div>
        <div className="grid grid-cols-2 items-start gap-2 sm:max-w-md">
          <SelectField
            label="Conviction"
            path={`${path}.conviction`}
            errors={errors}
            value={form.rigor.conviction}
            options={[
              { value: "", label: "Not stated" },
              ...[1, 2, 3, 4, 5].map((n) => ({
                value: String(n),
                label: `${n} of 5`,
              })),
            ]}
            onChange={(v) => setRigor({ conviction: v })}
          />
          <SelectField
            label="Main reason for owning it"
            path={`${path}.main_reason`}
            errors={errors}
            value={form.rigor.main_reason}
            options={[
              { value: "", label: "Not stated" },
              ...MAIN_REASONS.map((r) => ({ value: r.value, label: r.label })),
            ]}
            onChange={(v) => setRigor({ main_reason: v })}
          />
        </div>
      </fieldset>
      </MoreSettings>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">KPIs</legend>
        {form.kpis.length === 0 ? (
          <p className="text-sm text-text-muted">
            No KPIs: this holding gets no verdicts.
          </p>
        ) : null}
        {form.kpis.map((kpi, k) => {
          const kpiPath = `${path}.kpis.${k}`;
          return (
            <div
              key={kpi.key}
              className="grid grid-cols-2 items-start gap-2 rounded-card bg-surface p-3 sm:grid-cols-4 lg:grid-cols-[1.4fr_1fr_0.6fr_0.8fr_0.9fr_0.9fr_0.9fr_auto]"
            >
              <TextField
                label="Metric"
                path={`${kpiPath}.metric`}
                errors={errors}
                value={kpi.metric}
                list="kpi-metrics"
                onChange={(v) =>
                  setKpi(kpi.key, {
                    metric: v,
                    ...(kpi.id === slugFromMetric(kpi.metric)
                      ? { id: slugFromMetric(v) }
                      : {}),
                  })
                }
              />
              <TextField
                label="Id"
                path={`${kpiPath}.id`}
                errors={errors}
                value={kpi.id}
                onChange={(v) => setKpi(kpi.key, { id: v })}
              />
              <SelectField
                label="Operator"
                path={`${kpiPath}.operator`}
                errors={errors}
                value={kpi.operator}
                options={OPERATORS.map((o) => ({ value: o, label: o }))}
                onChange={(v) => setKpi(kpi.key, { operator: v })}
              />
              <TextField
                label="Threshold"
                path={`${kpiPath}.threshold`}
                errors={errors}
                value={kpi.threshold}
                inputMode="decimal"
                className="num"
                onChange={(v) => setKpi(kpi.key, { threshold: v })}
              />
              <TextField
                label="Unit"
                path={`${kpiPath}.unit`}
                errors={errors}
                value={kpi.unit}
                list="kpi-units"
                onChange={(v) => setKpi(kpi.key, { unit: v })}
              />
              <SelectField
                label="Basis"
                path={`${kpiPath}.basis`}
                errors={errors}
                value={kpi.basis}
                options={BASES.map((b) => ({ value: b, label: b }))}
                onChange={(v) => setKpi(kpi.key, { basis: v })}
              />
              <SelectField
                label="Period"
                path={`${kpiPath}.period`}
                errors={errors}
                value={kpi.period}
                options={KPI_PERIODS.map((p) => ({
                  value: p,
                  label: p === "ttm" ? "TTM" : p,
                }))}
                onChange={(v) => setKpi(kpi.key, { period: v })}
              />
              <Button
                variant="ghost"
                size="icon"
                className="self-end"
                aria-label={`Remove KPI ${kpi.id || k + 1}`}
                onClick={() =>
                  onChange((f) => ({
                    ...f,
                    kpis: f.kpis.filter((x) => x.key !== kpi.key),
                  }))
                }
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
              <TextField
                className="col-span-full"
                label="Description"
                path={`${kpiPath}.description`}
                errors={errors}
                value={kpi.description}
                onChange={(v) => setKpi(kpi.key, { description: v })}
                placeholder="Optional; tells the extraction what a custom metric means"
              />
            </div>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            onChange((f) => ({ ...f, kpis: [...f.kpis, newKpi()] }))
          }
        >
          <PlusIcon aria-hidden="true" />
          Add KPI
        </Button>
      </fieldset>
    </section>
  );
}
