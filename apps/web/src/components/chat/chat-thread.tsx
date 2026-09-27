"use client";

import {
  BotIcon,
  CheckIcon,
  CircleSlashIcon,
  LoaderCircleIcon,
  SendIcon,
  ShieldAlertIcon,
  UserIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { CitationLink } from "@/components/news/citation";
import { SourceLinksLine } from "@/components/sources/source-list";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type ActionOut,
  type ChatSentence,
  type ConversationDetail,
  type JobStatus,
  type MessageOut,
  type Source,
} from "@/lib/api/client";
import {
  ACTION_STATUS,
  actionLabel,
  actionsFor,
  MESSAGE_MAX,
  orphanActions,
  pendingMessage,
  pollDecision,
  sentenceView,
  sourceNumbering,
  validateMessage,
} from "@/lib/chat/chat";
import { formatDateTime } from "@/lib/format";
import { jobHref } from "@/lib/jobs/job-detail";
import { sourceKindLabel, sourcePreview } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

const TEXTAREA =
  "w-full resize-y rounded-md border border-border bg-surface-raised px-3 py-2 text-base text-text shadow-xs outline-none placeholder:text-text-muted md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal";

type Load = { kind: "loading" } | { kind: "error"; message: string; status?: number } | { kind: "ok"; detail: ConversationDetail };

/**
 * One conversation: the messages, each answer sentence by sentence with its sources, the
 * actions the assistant proposed (Confirm / Cancel), and a box to ask the next question.
 * While an answer is being written the conversation is re-read every ~2 s. With no
 * conversation yet, the first question creates one (`onCreated`).
 */
export function ChatThread({
  conversationId,
  seed,
  onSeedConsumed,
  onCreated,
  onChanged,
  className,
}: {
  conversationId: number | null;
  /** Pre-fills the composer once (e.g. "ask about this" from a selection), then is consumed. */
  seed?: string | null;
  onSeedConsumed?: () => void;
  onCreated?: (id: number) => void;
  /** A message was sent or answered (the conversation list can refresh). */
  onChanged?: () => void;
  className?: string;
}) {
  const [load, setLoad] = useState<Load | null>(null);
  const [jobStatus, setJobStatus] = useState<{ id: number; status: JobStatus; error: string | null } | null>(null);
  const [stopped, setStopped] = useState<string | null>(null);
  const waitingSince = useRef<Map<number, number>>(new Map());
  const sequence = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  const fetchDetail = useCallback(async () => {
    if (conversationId === null) return;
    const mine = ++sequence.current;
    try {
      const { data, response } = await getBrowserApiClient().GET("/chat/conversations/{conversation_id}", {
        params: { path: { conversation_id: conversationId } },
      });
      if (mine !== sequence.current) return;
      if (data) setLoad({ kind: "ok", detail: data });
      else
        setLoad((prev) =>
          prev?.kind === "ok" ? prev : { kind: "error", message: `The API answered HTTP ${response.status}.`, status: response.status },
        );
    } catch (err) {
      if (mine === sequence.current)
        setLoad((prev) => (prev?.kind === "ok" ? prev : { kind: "error", message: err instanceof Error ? err.message : String(err) }));
    }
  }, [conversationId]);

  useEffect(() => {
    queueMicrotask(() => {
      setLoad(conversationId === null ? null : { kind: "loading" });
      setStopped(null);
      setJobStatus(null);
    });
    void fetchDetail();
  }, [conversationId, fetchDetail]);

  const detail = load?.kind === "ok" ? load.detail : null;
  const pending = detail ? pendingMessage(detail.messages) : null;
  const lastCount = useRef(0);

  // Poll while an answer is pending; check its job after a while so a failed run stops the wait.
  useEffect(() => {
    if (!detail || stopped) return;
    const now = Date.now();
    if (pending && !waitingSince.current.has(pending.id)) waitingSince.current.set(pending.id, now);
    const decision = pollDecision(detail, {
      waitingSince: pending ? waitingSince.current.get(pending.id)! : null,
      now,
      jobStatus: pending && jobStatus?.id === pending.job_id ? jobStatus.status : null,
      jobError: jobStatus?.error,
    });
    if (decision.kind === "failed") {
      queueMicrotask(() => setStopped(`The answer could not be written: ${decision.reason}.`));
      return;
    }
    if (decision.kind === "timed-out") {
      queueMicrotask(() => setStopped("No answer after 5 minutes; stopped waiting. The job page says what happened."));
      return;
    }
    if (decision.kind !== "poll") return;
    const timer = setTimeout(() => {
      void fetchDetail();
      if (decision.checkJob && pending?.job_id) {
        const jobId = pending.job_id;
        getBrowserApiClient()
          .GET("/jobs/{job_id}", { params: { path: { job_id: jobId } } })
          .then(({ data }) => data && setJobStatus({ id: jobId, status: data.status, error: data.error }))
          .catch(() => undefined);
      }
    }, decision.delayMs);
    return () => clearTimeout(timer);
  }, [detail, pending, jobStatus, stopped, fetchDetail]);

  // Tell the parent when the number of messages changes (sent or answered); scroll to the end.
  useEffect(() => {
    const count = detail?.messages.length ?? 0;
    if (count !== lastCount.current) {
      if (lastCount.current !== 0) onChanged?.();
      lastCount.current = count;
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [detail, onChanged]);

  async function send(content: string): Promise<string | null> {
    const api = getBrowserApiClient();
    let id = conversationId;
    if (id === null) {
      const created = await api.POST("/chat/conversations", { body: { title: null } });
      if (!created.data) return `Could not start a conversation (HTTP ${created.response.status}).`;
      id = created.data.id;
    }
    const { data, response } = await api.POST("/chat/conversations/{conversation_id}/messages", {
      params: { path: { conversation_id: id } },
      body: { content: content.trim() },
    });
    if (!data) return `Not sent (HTTP ${response.status}).`;
    setStopped(null);
    waitingSince.current.set(data.message.id, Date.now());
    if (id !== conversationId) onCreated?.(id);
    else await fetchDetail();
    onChanged?.();
    return null;
  }

  async function decide(action: ActionOut, verb: "confirm" | "cancel"): Promise<string | null> {
    const api = getBrowserApiClient();
    const result =
      verb === "confirm"
        ? await api.POST("/chat/actions/{action_id}/confirm", { params: { path: { action_id: action.id } } })
        : await api.POST("/chat/actions/{action_id}/cancel", { params: { path: { action_id: action.id } } });
    await fetchDetail();
    if (result.data) return null;
    return result.response.status === 409 ? "Already decided (the list is refreshed)." : `The API answered HTTP ${result.response.status}.`;
  }

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", className)}>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1" aria-live="polite">
        {conversationId === null ? (
          <p className="text-sm text-text-muted">
            Ask about your portfolio, a holding, the news or any report. Every sentence of an answer links to its sources; a
            sentence with nothing to cite says &ldquo;no source&rdquo;. The assistant can propose a check or a research run,
            which starts only when you confirm it.
          </p>
        ) : null}
        {load?.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading the conversation…
          </p>
        ) : null}
        {load?.kind === "error" ? (
          <AlertMessage title={load.status === 404 ? "No such conversation" : "Could not load the conversation"}>{load.message}</AlertMessage>
        ) : null}
        {detail ? (
          <>
            {detail.messages.length === 0 ? <p className="text-sm text-text-muted">No messages yet.</p> : null}
            <ol className="space-y-4">
              {detail.messages.map((m) => (
                <li key={m.id}>
                  <Message message={m} actions={actionsFor(m, detail.actions)} onDecide={decide} />
                </li>
              ))}
            </ol>
            {orphanActions(detail.messages, detail.actions).length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs text-text-muted">Other proposed actions</p>
                {orphanActions(detail.messages, detail.actions).map((a) => (
                  <ActionCard key={a.id} action={a} onDecide={decide} />
                ))}
              </div>
            ) : null}
            {pending && !stopped ? (
              <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
                <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
                Writing the answer…
                {pending.job_id ? (
                  <Link href={jobHref(pending.job_id)} className="text-accent hover:underline">
                    job #{pending.job_id}
                  </Link>
                ) : null}
              </p>
            ) : null}
            {stopped ? (
              <AlertMessage title="No answer">
                {stopped}{" "}
                {pending?.job_id ? (
                  <Link href={jobHref(pending.job_id)} className="text-accent hover:underline">
                    Open job #{pending.job_id}
                  </Link>
                ) : null}
              </AlertMessage>
            ) : null}
          </>
        ) : null}
        <div ref={endRef} />
      </div>
      <Composer onSend={send} disabled={Boolean(pending && !stopped)} seed={seed} onSeedConsumed={onSeedConsumed} />
    </div>
  );
}

function Composer({
  onSend,
  disabled,
  seed,
  onSeedConsumed,
}: {
  onSend: (content: string) => Promise<string | null>;
  disabled: boolean;
  seed?: string | null;
  onSeedConsumed?: () => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [consumedSeed, setConsumedSeed] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // A new seed pre-fills the composer; adjusting state from a prop change belongs in
  // render, not an effect (react.dev/learn/you-might-not-need-an-effect).
  if (seed && seed !== consumedSeed) {
    setConsumedSeed(seed);
    setText(seed);
  }

  useEffect(() => {
    if (seed && seed === consumedSeed) {
      textareaRef.current?.focus();
      onSeedConsumed?.();
    }
  }, [seed, consumedSeed, onSeedConsumed]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const problem = validateMessage(text);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const failure = await onSend(text);
      if (failure) setError(failure);
      else setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!busy && !disabled) void submit();
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-1 border-t border-border pt-3">
      <label htmlFor={`${id}-q`} className="sr-only">
        Your question
      </label>
      <textarea
        ref={textareaRef}
        id={`${id}-q`}
        rows={2}
        value={text}
        maxLength={MESSAGE_MAX + 200}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
        className={TEXTAREA}
      />
      <div className="flex items-center justify-between gap-2">
        <p id={`${id}-err`} className="text-xs text-text">
          {error}
        </p>
        <Button type="submit" size="sm" disabled={busy || disabled}>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SendIcon aria-hidden="true" />}
          {disabled ? "Waiting for the answer" : "Send"}
        </Button>
      </div>
    </form>
  );
}

function Message({
  message: m,
  actions,
  onDecide,
}: {
  message: MessageOut;
  actions: ActionOut[];
  onDecide: (action: ActionOut, verb: "confirm" | "cancel") => Promise<string | null>;
}) {
  const numbering = useMemo(() => sourceNumbering(m.answer?.sources ?? []), [m.answer]);
  const Icon = m.role === "user" ? UserIcon : BotIcon;
  return (
    <article aria-label={m.role === "user" ? "Your message" : "Answer"} className={cn("space-y-2 rounded-lg p-3 text-sm", m.role === "user" ? "bg-surface-muted" : "border border-border bg-surface-raised")}>
      <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
        <span className="inline-flex items-center gap-1 font-medium">
          <Icon aria-hidden="true" className="size-3.5" />
          {m.role === "user" ? "You" : "Assistant"}
          {m.answer?.model ? <span className="font-mono font-normal">· {m.answer.model}</span> : null}
        </span>
        <time dateTime={m.created_at} className="num">
          {formatDateTime(m.created_at)}
        </time>
      </p>
      {m.role === "user" || !m.answer ? (
        <p className="whitespace-pre-line">{m.content}</p>
      ) : m.answer.shape === "low_evidence" ? (
        <LowEvidenceAnswer sources={m.answer.sources} note={m.answer.note} />
      ) : (
        <div className="space-y-1.5">
          {m.answer.sentences.length === 0 ? <p className="text-text-muted">{m.content}</p> : null}
          <p className="leading-relaxed">
            {m.answer.sentences.map((s, i) => (
              <AnswerSentence key={i} sentence={s} numbering={numbering} />
            ))}
          </p>
          {m.answer.note ? <p className="text-xs text-text-muted">{m.answer.note}</p> : null}
        </div>
      )}
      {m.status === "failed" ? (
        <p className="flex items-center gap-1 text-xs text-text">
          <ShieldAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
          The assistant could not answer.
          {m.job_id ? (
            <Link href={jobHref(m.job_id)} className="text-accent hover:underline">
              job #{m.job_id}
            </Link>
          ) : null}
        </p>
      ) : null}
      {m.answer && m.answer.shape !== "low_evidence" && m.answer.sources.length > 0 ? <AnswerSources sources={m.answer.sources} /> : null}
      {m.answer && m.answer.tools_used.length > 0 ? (
        <p className="text-xs text-text-muted">Looked at: {m.answer.tools_used.map((t) => t.replaceAll("_", " ")).join(", ")}</p>
      ) : null}
      {actions.map((a) => (
        <ActionCard key={a.id} action={a} onDecide={onDecide} />
      ))}
    </article>
  );
}

function AnswerSentence({ sentence, numbering }: { sentence: ChatSentence; numbering: Map<string, { number: number; source: Source }> }) {
  const view = sentenceView(sentence, numbering);
  return (
    <span>
      <span className={view.kind === "unsupported" ? "underline decoration-warning-signal decoration-wavy underline-offset-4" : undefined}>
        {sentence.text}
      </span>
      {view.kind !== "no_source" ? (
        <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
          {view.sources.map((c) =>
            c.source && c.href ? (
              <CitationLink
                key={c.id}
                href={c.href}
                label={String(c.number)}
                title={`${c.source.title} (${sourceKindLabel(c.source.kind)})`}
                external={c.external}
                preview={sourcePreview(c.source)}
              />
            ) : (
              <span key={c.id} className="rounded-sm border border-dashed border-border px-1 text-xs text-text-muted" title="No link stored for this source">
                {c.number ?? c.id}
              </span>
            ),
          )}
        </span>
      ) : (
        <span className="ml-1 inline-flex items-center gap-0.5 rounded-sm border border-dashed border-border px-1 align-baseline text-xs text-text-muted" title="Nothing checkable cites this sentence">
          <CircleSlashIcon aria-hidden="true" className="size-3" />
          no source
        </span>
      )}
      {view.kind === "unsupported" ? (
        <span className="ml-1 inline-flex items-center gap-1 rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1 align-baseline text-xs text-text">
          <ShieldAlertIcon aria-hidden="true" className="size-3 text-warning-signal" />
          Unsupported: {view.reason}
        </span>
      ) : null}{" "}
    </span>
  );
}

function SourceCard({ source: s, index }: { source: Source; index: number }) {
  return (
    <li className="space-y-0.5 rounded-md border border-border p-2">
      <p className="flex justify-between gap-2 text-text-muted">
        <span className="num">
          [{index + 1}] {sourceKindLabel(s.kind)}
        </span>
        <span>{typeof s.tier === "number" ? `Tier ${s.tier}` : "app record"}</span>
      </p>
      <p className="font-medium text-text">{s.title}</p>
      {s.as_of ? <p className="num text-text-muted">As of {s.as_of}</p> : null}
      <SourceLinksLine source={s} />
    </li>
  );
}

function AnswerSources({ sources }: { sources: Source[] }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-text-muted">Sources ({sources.length})</summary>
      <ol className="mt-2 space-y-2">
        {sources.map((s, i) => (
          <SourceCard key={s.id} source={s} index={i} />
        ))}
      </ol>
    </details>
  );
}

/**
 * Graceful degradation (Ledger II §4.5): shown instead of prose when nothing in the draft
 * held up as a checked, sourced claim. A fluent-sounding paragraph marked unsupported after
 * the fact reads as confident despite having no backing; this shows the raw sources found,
 * unformatted, so the owner sees exactly how little there was to go on.
 */
function LowEvidenceAnswer({ sources, note }: { sources: Source[]; note: string | null | undefined }) {
  return (
    <div className="space-y-2 rounded-md border border-dashed border-border p-2.5 text-sm">
      <p className="flex items-center gap-1.5 font-medium">
        <ShieldAlertIcon aria-hidden="true" className="size-4 shrink-0 text-warning-signal" />
        Not enough evidence to answer this as a claim.
      </p>
      {note ? <p className="text-xs text-text-muted">{note}</p> : null}
      {sources.length === 0 ? (
        <p className="text-xs text-text-muted">No sources were found for it either.</p>
      ) : (
        <>
          <p className="text-xs text-text-muted">Here&rsquo;s what was found, as stored — not written up as prose:</p>
          <ol className="space-y-2 text-xs">
            {sources.map((s, i) => (
              <SourceCard key={s.id} source={s} index={i} />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

function ActionCard({ action: a, onDecide }: { action: ActionOut; onDecide: (action: ActionOut, verb: "confirm" | "cancel") => Promise<string | null> }) {
  const [busy, setBusy] = useState<"confirm" | "cancel" | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(verb: "confirm" | "cancel") {
    setBusy(verb);
    setError(null);
    try {
      setError(await onDecide(a, verb));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }
  return (
    <div role="group" aria-label={`Proposed action: ${actionLabel(a)}`} className="space-y-1.5 rounded-md border border-border bg-surface-muted p-2 text-sm">
      <p className="font-medium">{actionLabel(a)}</p>
      {a.reason ? <p className="text-xs text-text-muted">{a.reason}</p> : null}
      <p className="text-xs text-text-muted">{ACTION_STATUS[a.status]}</p>
      {a.status === "proposed" ? (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => void run("confirm")} disabled={busy !== null}>
            {busy === "confirm" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
            Confirm
          </Button>
          <Button size="sm" variant="outline" onClick={() => void run("cancel")} disabled={busy !== null}>
            <XIcon aria-hidden="true" />
            Cancel
          </Button>
        </div>
      ) : null}
      {a.status === "confirmed" && a.job_id !== null ? <ConfirmedJob jobId={a.job_id} /> : null}
      {error ? <p className="text-xs text-text">{error}</p> : null}
    </div>
  );
}

function ConfirmedJob({ jobId }: { jobId: number }) {
  const [status, setStatus] = useState<JobStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/jobs/{job_id}", { params: { path: { job_id: jobId } } })
      .then(({ data }) => !cancelled && data && setStatus(data.status))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [jobId]);
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs">
      Started as{" "}
      <Link href={jobHref(jobId)} className="text-accent hover:underline">
        job #{jobId}
      </Link>
      {status ? <JobStatusBadge status={status} /> : null}
    </p>
  );
}
