"use client";

import { CalendarDaysIcon, CheckIcon, LoaderCircleIcon, PencilIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfigHeader } from "@/components/config/config-header";
import { MoreSettings, SummaryPanel } from "@/components/config/summary";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ExchangeCalendar } from "@/lib/api/client";
import { historyHref } from "@/lib/config/domains";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { CALENDAR_NOTE, closureKey, isoToday, isPast, selectedClosures, sortClosures } from "@/lib/reference/calendar";

import { SessionsSection } from "./sessions-section";

const fetchCalendar = () => getBrowserApiClient().GET("/calendar/exchanges");
const LINK = "text-accent-ink underline-offset-4 hover:underline";

/**
 * Exchange calendar: the closures confirmed into the Jobs config, and the public holidays
 * in each holding exchange's country, suggested as closures for the owner to confirm.
 */
export function CalendarView() {
  const { state, reload } = useApiResource(fetchCalendar);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <div className="space-y-6">
      <ConfigHeader title="Exchange calendar" domain="jobs">
        <p>
          Days an exchange is closed, so a sync or collector with nothing to collect raises no missed-run alert and a
          missing price is not reported as stale. Confirmed closures are stored in the Jobs config and used from the
          next run, with no deploy.
        </p>
      </ConfigHeader>
      <AlertMessage tone="info" title="Public holidays are not exchange calendars">
        {CALENDAR_NOTE}
      </AlertMessage>

      <HolidayRefresh onDone={reload} />

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the calendar…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}
      {data ? <CalendarContent data={data} reload={reload} /> : null}

      <p className="text-sm text-text-muted">
        Instruments without a resolved home market have no exchange here; see the{" "}
        <Link href="/status#reference-gaps" className={LINK}>
          reference data gaps
        </Link>
        .
      </p>
    </div>
  );
}

function HolidayRefresh({ onDone }: { onDone: () => void }) {
  const [status, setStatus] = useState<{ busy: boolean; text: string } | null>(null);
  async function run() {
    setStatus({ busy: true, text: "Queueing…" });
    try {
      const r = await getBrowserApiClient().POST("/jobs/holiday-refresh");
      if (r.data) {
        setStatus({ busy: false, text: `Queued as job #${r.data.id}. New suggestions appear here when it finishes; reload in a minute.` });
        setTimeout(onDone, 30_000);
      } else {
        setStatus({ busy: false, text: `Not queued (HTTP ${r.response.status}).` });
      }
    } catch (err) {
      setStatus({ busy: false, text: err instanceof Error ? err.message : String(err) });
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-card bg-surface-muted p-3">
      <Button size="sm" variant="outline" onClick={() => void run()} disabled={status?.busy}>
        {status?.busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CalendarDaysIcon aria-hidden="true" />}
        Refresh holidays
      </Button>
      <Button size="sm" variant="ghost" onClick={onDone}>
        <RefreshCwIcon aria-hidden="true" />
        Reload
      </Button>
      <p role="status" className="text-sm text-text-muted">
        {status?.text ?? "Loads this and next year's public holidays from Nager.Date for the countries in Config: Jobs."}
      </p>
    </div>
  );
}

function CalendarContent({ data, reload }: { data: ExchangeCalendar; reload: () => void }) {
  const exchanges = Object.entries(data.exchanges).sort(([a], [b]) => a.localeCompare(b));
  const upcoming = sortClosures(data.closures).filter((r) => !isPast(r.date, isoToday()));
  return (
    <>
      <SummaryPanel
        title="Next closures by exchange"
        reading={
          exchanges.length === 0
            ? "No holding has a resolved home market yet."
            : `${exchanges.length} exchange${exchanges.length === 1 ? "" : "s"}; ${upcoming.length} confirmed closure${upcoming.length === 1 ? "" : "s"} ahead, ${data.suggestions.length} suggested day${data.suggestions.length === 1 ? "" : "s"} waiting.`
        }
      >
        {exchanges.length > 0 ? (
          <ul aria-label="Next closures by exchange" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {exchanges.map(([mic, country]) => {
              const next = upcoming.filter((r) => r.mic === mic).slice(0, 3);
              return (
                <li key={mic} className="rounded-control bg-surface px-3 py-2 text-sm">
                  <p className="font-medium">
                    {mic} <span className="font-normal text-text-muted">{country}</span>
                  </p>
                  {next.length === 0 ? (
                    <p className="text-text-muted">No closures confirmed ahead.</p>
                  ) : (
                    <ul className="mt-1 space-y-0.5">
                      {next.map((r) => (
                        <li key={closureKey(r)} className="flex justify-between gap-2">
                          <span className="truncate">{r.name}</span>
                          <span className="num shrink-0 text-text-muted">{formatDate(r.date)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        ) : null}
      </SummaryPanel>
      <MoreSettings
        title="Confirm suggested closures"
        hint={`${data.suggestions.length} public holiday${data.suggestions.length === 1 ? "" : "s"} waiting`}
      >
        <Suggestions data={data} reload={reload} />
      </MoreSettings>
      <Confirmed data={data} />
      <SessionsSection mics={exchanges.map(([mic]) => mic)} />
    </>
  );
}

function Suggestions({ data, reload }: { data: ExchangeCalendar; reload: () => void }) {
  const rows = useMemo(() => sortClosures(data.suggestions), [data.suggestions]);
  const today = isoToday();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string; version?: number } | null>(null);
  const body = selectedClosures(rows, selected);
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(closureKey(r)));

  const toggle = useCallback((key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  async function confirm() {
    if (!body) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await getBrowserApiClient().POST("/calendar/exchanges/closures", { body });
      if (r.data) {
        setResult({
          ok: true,
          text: `Confirmed ${body.closures.length} closure${body.closures.length === 1 ? "" : "s"}. Saved as jobs version #${r.data.jobs_version_id}; used from the next run.`,
          version: r.data.jobs_version_id,
        });
        setSelected(new Set());
        reload();
      } else {
        const detail = r.error && typeof r.error === "object" && "detail" in r.error ? JSON.stringify(r.error.detail) : "";
        setResult({ ok: false, text: `Not saved (HTTP ${r.response.status}). ${detail}` });
      }
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="suggested-heading" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="suggested-heading" className="panel-title">
            Suggested closures
          </h2>
          <p className="text-sm text-text-muted">
            Public holidays in each exchange&rsquo;s country that are not confirmed yet. Tick only the days the exchange is
            closed.
          </p>
        </div>
        <Button onClick={() => void confirm()} disabled={!body || busy}>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
          Confirm selected <span className="num">({body?.closures.length ?? 0})</span>
        </Button>
      </div>
      <div role="status" aria-live="polite">
        {result ? (
          result.ok ? (
            <p className="rounded-card bg-surface p-3 text-sm">
              {result.text}{" "}
              <Link href={historyHref("jobs")} className={LINK}>
                Jobs history
              </Link>
            </p>
          ) : (
            <AlertMessage title="Not saved">{result.text}</AlertMessage>
          )
        ) : null}
      </div>
      {rows.length === 0 ? (
        <EmptyState title="No suggestions">
          Every public holiday loaded for the holding exchanges&rsquo; countries is already confirmed, or none are
          loaded yet: use <strong>Refresh holidays</strong>.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-card bg-surface">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="sr-only">Suggested exchange closures; select the days the exchange is closed</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="w-10 px-3 py-2 text-left font-medium">
                  <input
                    type="checkbox"
                    aria-label={allSelected ? "Clear the selection" : "Select every suggestion"}
                    checked={allSelected}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map(closureKey)))}
                    className="size-4 accent-accent"
                  />
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Date</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Exchange</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Public holiday</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const key = closureKey(r);
                const past = isPast(r.date, today);
                return (
                  <tr key={key} className="border-b border-border last:border-0">
                    <td className="px-3 py-1.5">
                      <input
                        type="checkbox"
                        checked={selected.has(key)}
                        onChange={() => toggle(key)}
                        aria-label={`${r.name}, ${r.mic}, ${formatDate(r.date)}`}
                        className="size-4 accent-accent"
                      />
                    </td>
                    <td className="num px-3 py-1.5 text-right whitespace-nowrap">
                      {formatDate(r.date)}
                      {past ? <span className="ml-1 text-xs text-text-muted">(past)</span> : null}
                    </td>
                    <td className="px-3 py-1.5">
                      <span>{r.mic}</span>
                      {data.exchanges[r.mic] ? <span className="text-text-muted"> · {data.exchanges[r.mic]}</span> : null}
                    </td>
                    <td className="px-3 py-1.5">{r.name}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Confirmed({ data }: { data: ExchangeCalendar }) {
  const rows = sortClosures(data.closures);
  return (
    <section aria-labelledby="confirmed-heading" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="confirmed-heading" className="panel-title">
            Confirmed closures
          </h2>
          <p className="text-sm text-text-muted">In the Jobs config (Holiday calendar); edit or remove them there.</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/config/jobs#calendar-heading">
            <PencilIcon aria-hidden="true" />
            Edit in Config: Jobs
          </Link>
        </Button>
      </div>
      {rows.length === 0 ? (
        <EmptyState title="No closures confirmed">Confirm suggested days above, or add them in Config: Jobs.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-card bg-surface-muted">
          <table className="w-full min-w-[28rem] text-sm">
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="px-3 py-2 text-right font-medium">Date</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Exchange</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Name</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={closureKey(r)} className="border-b border-border last:border-0">
                  <td className="num px-3 py-1.5 text-right whitespace-nowrap">{formatDate(r.date)}</td>
                  <td className="px-3 py-1.5">{r.mic}</td>
                  <td className="px-3 py-1.5">{r.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
