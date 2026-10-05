"use client";

import { useCallback, useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import { isoToday } from "@/lib/reference/calendar";
import { apiErrorText } from "@/lib/reference/form-utils";
import {
  emptySessionDraft,
  needsTimes,
  parseSessions,
  SESSION_STATE,
  sessionRequest,
  sessionWindowError,
  stateCounts,
  validateSession,
  type SessionDraft,
  type SessionRow,
} from "@/lib/reference/forms";

const LINK = "text-accent-ink underline-offset-4 hover:underline";

const shiftDays = (iso: string, days: number) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

/**
 * Sessions recorded by hand for one exchange: a half day or an ad hoc closure confirmed from
 * the exchange's own notice. These override the weekday-less-holidays assumption used for
 * return-window coverage and price freshness. State is shown as a glyph and words.
 */
export function SessionsSection({ mics, onRecorded }: { mics: string[]; onRecorded?: () => void }) {
  const today = isoToday();
  const [mic, setMic] = useState(mics[0] ?? "");
  const [start, setStart] = useState(() => shiftDays(today, -30));
  const [end, setEnd] = useState(() => shiftDays(today, 60));
  const windowError = sessionWindowError(start, end);
  const micOk = /^[A-Z0-9]{4}$/.test(mic.trim().toUpperCase());
  const cleanMic = mic.trim().toUpperCase();

  const enabled = micOk && windowError === null;
  const fetcher = useCallback(async (): Promise<ApiResult<unknown>> => {
    if (!enabled) return { data: [], response: new Response(null, { status: 200 }) };
    return getBrowserApiClient().GET("/exchange-sessions/{mic}", { params: { path: { mic: cleanMic }, query: { start, end } } });
  }, [enabled, cleanMic, start, end]);
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => parseSessions(raw), [raw]);
  const counts = useMemo(() => stateCounts(rows), [rows]);

  return (
    <section aria-labelledby="sessions-heading" className="space-y-3">
      <div>
        <h2 id="sessions-heading" className="text-[22px] leading-tight font-semibold">
          Recorded sessions
        </h2>
        <p className="max-w-[72ch] text-sm text-text-muted">
          Half days and ad hoc closures you confirmed from an exchange notice. Where a day is recorded here it is used
          instead of the assumption that the exchange trades on every weekday that is not a confirmed holiday.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Exchange (market code)" error={mic !== "" && !micOk ? "4 letters or digits, such as XNYS." : undefined}>
          <Input list="session-mics" value={mic} onChange={(e) => setMic(e.target.value.toUpperCase())} maxLength={4} className="num w-32" />
        </Field>
        <datalist id="session-mics">
          {mics.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <Field label="From">
          <Input type="date" value={start} onChange={(e) => e.target.value && setStart(e.target.value)} className="w-40" />
        </Field>
        <Field label="To" error={windowError ?? undefined}>
          <Input type="date" value={end} onChange={(e) => e.target.value && setEnd(e.target.value)} className="w-40" />
        </Field>
        <Button type="button" variant="outline" size="sm" onClick={() => reload()} disabled={!enabled}>
          Reload
        </Button>
      </div>

      <div role="status" aria-live="polite" className="text-sm text-text-muted">
        {state.kind === "loading" ? "Loading sessions…" : null}
        {state.kind === "error" ? `Could not load sessions: ${state.title}${state.detail ? `. ${state.detail}` : ""}` : null}
      </div>

      {enabled && state.kind === "ok" && rows.length === 0 ? (
        <EmptyState title="No sessions recorded in this window">
          Nothing is stored for {cleanMic} between {formatDate(start)} and {formatDate(end)}, so the weekday-and-holiday
          assumption applies. Record a half day or closure below.
        </EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <>
          <ul className="flex flex-wrap gap-2 text-sm" aria-label="Sessions by state">
            {counts.map((c) => (
              <li key={c.state} className="border border-border px-2 py-1">
                <span aria-hidden="true">{SESSION_STATE[c.state].glyph} </span>
                {SESSION_STATE[c.state].label} <span className="num font-semibold">{c.count}</span>
              </li>
            ))}
          </ul>
          <div className="overflow-x-auto border border-border">
            <table className="w-full min-w-[40rem] text-sm">
              <caption className="sr-only">Recorded exchange sessions for {cleanMic}</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-right font-normal">Date</th>
                  <th scope="col" className="px-3 py-2 font-normal">State</th>
                  <th scope="col" className="px-3 py-2 font-normal">Open to close, UTC</th>
                  <th scope="col" className="px-3 py-2 font-normal">Source</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <SessionLine key={r.key} row={r} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <SessionForm
        defaultMic={micOk ? cleanMic : (mics[0] ?? "")}
        onSaved={() => {
          reload();
          onRecorded?.();
        }}
      />
    </section>
  );
}

const hhmm = (iso: string | null) => (iso ? formatDateTime(iso, { timeZone: "UTC" }) : null);

function SessionLine({ row }: { row: SessionRow }) {
  const meta = row.state === "unknown" ? null : SESSION_STATE[row.state];
  return (
    <tr className="border-b border-border-soft align-top last:border-0">
      <td className="num px-3 py-2 text-right whitespace-nowrap">{row.date ? formatDate(row.date) : "Unknown"}</td>
      <td className="px-3 py-2">
        <span className="font-medium">
          <span aria-hidden="true">{meta?.glyph ?? "?"} </span>
          {meta?.label ?? "Unknown"}
        </span>
        {row.reason ? <div className="max-w-[30ch] text-xs text-text-muted">{row.reason}</div> : null}
      </td>
      <td className="num px-3 py-2 text-xs">
        {row.state === "closed" ? "No trading" : row.openAt && row.closeAt ? `${hhmm(row.openAt)} to ${hhmm(row.closeAt)}` : "Times not recorded"}
      </td>
      <td className="px-3 py-2 text-xs">
        {row.confirmed ? <OwnerTag>Confirmed by you</OwnerTag> : <span className="text-text-muted">Not confirmed</span>}
        <div className="text-text-muted">
          {row.source ?? "Unknown"}
          {row.evidenceUrl ? (
            <>
              {" "}
              <a href={row.evidenceUrl} target="_blank" rel="noreferrer" className={LINK}>
                Notice
              </a>
            </>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

function SessionForm({ defaultMic, onSaved }: { defaultMic: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<SessionDraft>(() => ({ ...emptySessionDraft(), mic: defaultMic }));
  const [touched, setTouched] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validateSession(draft);
  const set = <K extends keyof SessionDraft>(k: K, v: SessionDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof SessionDraft) => (touched ? errors[k] : undefined);
  const times = needsTimes(draft.state);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().POST("/exchange-sessions", { body: sessionRequest(draft) });
      if (r.response.ok) {
        setSave({ kind: "ok", text: `Recorded ${SESSION_STATE[draft.state].label.toLowerCase()} for ${draft.mic.trim().toUpperCase()} on ${formatDate(draft.date)}.` });
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
        <Button type="button" variant="outline" size="sm" onClick={() => { setDraft((d) => ({ ...d, mic: d.mic || defaultMic })); setOpen(true); }}>
          Record a session
        </Button>
        <SaveNote state={save} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Record an exchange session">
      <FormFrame title="Record a session from an exchange notice">
        <p className="max-w-[68ch] text-xs text-text-muted">
          <OwnerTag>Confirmed by you</OwnerTag> Recording the same exchange, date and source again replaces the earlier row.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Exchange (market code)" error={show("mic")}>
            <Input value={draft.mic} onChange={(e) => set("mic", e.target.value.toUpperCase())} maxLength={4} className="num" placeholder="XNYS" />
          </Field>
          <Field label="Session date" error={show("date")}>
            <Input type="date" value={draft.date} onChange={(e) => set("date", e.target.value)} />
          </Field>
          <Field label="State" hint={SESSION_STATE[draft.state].meaning}>
            <select value={draft.state} onChange={(e) => set("state", e.target.value as SessionDraft["state"])} className={SELECT_CLASS}>
              <option value="half_day">{SESSION_STATE.half_day.glyph} Half day</option>
              <option value="closed">{SESSION_STATE.closed.glyph} Closed</option>
              <option value="open">{SESSION_STATE.open.glyph} Open</option>
            </select>
          </Field>
          <Field label="Source" hint="Who published it, such as the exchange name." error={show("source")}>
            <Input value={draft.source} onChange={(e) => set("source", e.target.value)} maxLength={80} />
          </Field>
          {times ? (
            <>
              <Field label="Opens (UTC)" error={show("openAt")}>
                <Input type="datetime-local" value={draft.openAt} onChange={(e) => set("openAt", e.target.value)} className="num" />
              </Field>
              <Field label="Closes (UTC)" error={show("closeAt")}>
                <Input type="datetime-local" value={draft.closeAt} onChange={(e) => set("closeAt", e.target.value)} className="num" />
              </Field>
            </>
          ) : null}
          <Field label="Notice address" hint="The exchange page that confirms it." error={show("evidenceUrl")} className="sm:col-span-2">
            <Input type="url" value={draft.evidenceUrl} onChange={(e) => set("evidenceUrl", e.target.value)} placeholder="https://" maxLength={2000} />
          </Field>
          <Field label="Reason" hint="Optional, up to 300 characters, such as early close." error={show("reason")} className="sm:col-span-2">
            <Input value={draft.reason} onChange={(e) => set("reason", e.target.value)} maxLength={300} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.kind === "saving"}>
            Record session
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
