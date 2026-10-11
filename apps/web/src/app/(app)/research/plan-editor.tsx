"use client";

import { ArrowDownIcon, ArrowUpIcon, CheckIcon, LoaderCircleIcon, PlayIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { useResearchProfiles } from "@/components/research/research-starter";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { apiErrorDetail } from "@/lib/evidence/review";
import {
  authorLine,
  isDirty,
  localProblems,
  moveQuestion,
  removeQuestion,
  rewordQuestion,
  SOURCE_GROUPS,
  SOURCE_GROUP_LABELS,
  statusLabel,
  toEdits,
  toggleGroup,
  type DraftQuestion,
  type PlanDraft,
} from "@/lib/research/plan-editor";
import { sectionLabel } from "@/lib/research/report";

type Planner = "profile" | "model" | "code";

/**
 * Review a research plan before the run: draft it (by a model or by code), reword, reorder or
 * remove its questions, switch source groups on and off, approve, then start the run from it.
 * The run stores the plan it used and whether you edited it.
 */
export function PlanEditor({ initialTicker, onStarted }: { initialTicker: string; onStarted: () => void }) {
  const [ticker, setTicker] = useState(initialTicker);
  const [planner, setPlanner] = useState<Planner>("model");
  const profiles = useResearchProfiles();
  const [profile, setProfile] = useState<string | null>(null);
  const chosenProfile = profile ?? profiles.defaultId;
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [busy, setBusy] = useState<"draft" | "save" | "start" | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const webAllowed = (draft?.plan.stop?.max_searches ?? 1) > 0;
  const locked = draft !== null && (draft.status === "started" || draft.status === "discarded");
  const dirty = draft !== null && isDirty(draft.plan.questions, questions);

  function show(next: PlanDraft) {
    setDraft(next);
    setQuestions(next.plan.questions);
    setProblems([]);
  }

  async function create() {
    setBusy("draft");
    setMessage(null);
    setProblems([]);
    try {
      const { data, error, response } = await getBrowserApiClient().POST("/research/plans", {
        body: { ticker: ticker.trim().toUpperCase(), profile: chosenProfile, planner },
      });
      if (data) show(data as unknown as PlanDraft);
      else setMessage(`The plan could not be drafted (HTTP ${response.status}). ${apiErrorDetail(error)}`.trim().slice(0, 300));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function save(approve: boolean) {
    if (!draft) return;
    const local = localProblems(questions, webAllowed);
    setProblems(local);
    if (local.length > 0) return;
    setBusy("save");
    setMessage(null);
    try {
      const { data, error, response } = await getBrowserApiClient().PATCH("/research/plans/{draft_id}", {
        params: { path: { draft_id: draft.id } },
        // Unchanged questions are not sent, so approving alone does not mark the plan as edited.
        body: { questions: dirty ? toEdits(questions) : undefined, approve },
      });
      if (data) {
        show(data as unknown as PlanDraft);
        setMessage(approve ? "Plan approved. You can start the run." : "Plan saved. Approve it to run.");
      } else {
        const detail = (error as { detail?: { problems?: string[] } } | undefined)?.detail;
        setProblems(detail?.problems ?? [`The plan was not saved (HTTP ${response.status}).`]);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    if (!draft) return;
    setBusy("start");
    setMessage(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/research/plans/{draft_id}/start", {
        params: { path: { draft_id: draft.id } },
      });
      if (data) {
        setDraft({ ...draft, status: "started" });
        setMessage("The run is queued with this plan.");
        onStarted();
      } else setMessage(`The run did not start (HTTP ${response.status}). Approve the plan first.`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Blueprint aria-labelledby="plan-editor-heading" className="flex flex-col gap-3 px-5 py-[18px]">
      <h2 id="plan-editor-heading" className="kicker font-sans">
        Review the plan first
      </h2>
      <p className="text-xs text-text-muted">
        Draft the research questions, edit them, and approve the plan before any search or model call is spent on the report.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-text-muted">
          Ticker
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            className="h-8 w-40 border border-border bg-surface px-2 font-mono text-sm text-text"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-text-muted">
          Profile
          <select
            value={chosenProfile}
            onChange={(e) => setProfile(e.target.value)}
            className="h-8 border border-border bg-surface px-2 text-sm text-text"
          >
            {profiles.options.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.id === profiles.defaultId ? " · default" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-text-muted">
          Written by
          <select
            value={planner}
            onChange={(e) => setPlanner(e.target.value as Planner)}
            className="h-8 border border-border bg-surface px-2 text-sm text-text"
          >
            <option value="model">A model (checked in code)</option>
            <option value="code">Code</option>
            <option value="profile">As the profile says</option>
          </select>
        </label>
        <Button size="sm" variant="outline" disabled={busy !== null || ticker.trim() === ""} onClick={() => void create()}>
          {busy === "draft" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
          Draft a plan
        </Button>
      </div>

      {message ? <AlertMessage title="Plan">{message}</AlertMessage> : null}

      {draft ? (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-text-muted">
            {statusLabel(draft.status)}. {authorLine(draft.plan)}
            {draft.plan.edited_by_owner || draft.edited ? " You have edited it." : ""}
          </p>
          {(draft.plan.notes ?? []).length > 0 ? (
            <ul className="list-disc pl-5 text-xs text-text-muted">
              {(draft.plan.notes ?? []).map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
          <ol className="space-y-3">
            {questions.map((q, i) => (
              <li key={q.id} className="flex flex-col gap-1.5 border-t border-border-soft pt-2">
                <div className="flex items-center justify-between gap-2 text-xs text-text-muted">
                  <span>{q.section ? sectionLabel(q.section) : "Whole report"}</span>
                  <span className="flex gap-1" data-print-hide>
                    <Button size="sm" variant="ghost" disabled={locked || i === 0} onClick={() => setQuestions((l) => moveQuestion(l, q.id, -1))}>
                      <ArrowUpIcon aria-hidden="true" />
                      <span className="sr-only">Move up</span>
                    </Button>
                    <Button size="sm" variant="ghost" disabled={locked || i === questions.length - 1} onClick={() => setQuestions((l) => moveQuestion(l, q.id, 1))}>
                      <ArrowDownIcon aria-hidden="true" />
                      <span className="sr-only">Move down</span>
                    </Button>
                    <Button size="sm" variant="ghost" disabled={locked} onClick={() => setQuestions((l) => removeQuestion(l, q.id))}>
                      <Trash2Icon aria-hidden="true" />
                      <span className="sr-only">Remove this question</span>
                    </Button>
                  </span>
                </div>
                <textarea
                  value={q.question}
                  disabled={locked}
                  rows={2}
                  aria-label={`Question for ${q.section ? sectionLabel(q.section) : "the whole report"}`}
                  onChange={(e) => setQuestions((l) => rewordQuestion(l, q.id, e.target.value))}
                  className="w-full border border-border bg-surface p-2 text-sm"
                />
                <fieldset className="flex flex-wrap gap-x-3 gap-y-1 text-xs" disabled={locked}>
                  <legend className="sr-only">Source groups for this question</legend>
                  {SOURCE_GROUPS.filter((g) => g !== "web" || webAllowed).map((g) => (
                    <label key={g} className="flex items-center gap-1">
                      <input type="checkbox" checked={q.source_groups.includes(g)} onChange={() => setQuestions((l) => toggleGroup(l, q.id, g))} />
                      {SOURCE_GROUP_LABELS[g]}
                    </label>
                  ))}
                </fieldset>
              </li>
            ))}
          </ol>
          {problems.length > 0 ? (
            <ul className="list-disc pl-5 text-sm text-danger" role="alert">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          ) : null}
          <div className="flex flex-wrap gap-2" data-print-hide>
            <Button size="sm" variant="outline" disabled={busy !== null || locked || !dirty} onClick={() => void save(false)}>
              Save changes
            </Button>
            <Button size="sm" variant="outline" disabled={busy !== null || locked || (draft.status === "approved" && !dirty)} onClick={() => void save(true)}>
              {busy === "save" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
              Approve plan
            </Button>
            <Button size="sm" disabled={busy !== null || draft.status !== "approved" || dirty} onClick={() => void start()}>
              {busy === "start" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
              Run with this plan
            </Button>
          </div>
        </div>
      ) : null}
    </Blueprint>
  );
}
