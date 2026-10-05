"use client";

import { useCallback, useId, useMemo, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";

import { SrTable } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  COVERAGE_LIMITS,
  parseCoverage,
  parseExpected,
  sessionCounts,
  stripLayout,
  validateCoverage,
  type CoverageDraft,
  type CoverageErrors,
  type ExpectedInfo,
  type SessionCell,
  type SessionStateName,
} from "@/lib/reference/price-coverage";

// ---------------------------------------------------------------- calendar strip

const STATE_LABEL: Record<SessionStateName, string> = {
  observed: "Observed",
  missing: "Missing",
  disagrees: "Disagrees with another source",
};

/**
 * One square per session. State is carried by fill pattern as well as colour: solid = observed,
 * hollow dashed = missing, diagonal half-fill = disagrees; a ring marks a confirmed special day.
 */
function SessionSquare({ state, special, style, title }: { state: SessionStateName; special?: boolean; style?: CSSProperties; title?: string }) {
  return (
    <span
      aria-hidden="true"
      title={title}
      style={
        state === "disagrees"
          ? { ...style, backgroundImage: "linear-gradient(135deg, var(--warning-signal) 50%, transparent 50%)" }
          : style
      }
      className={cn(
        "block size-3.5 border",
        state === "observed" && "border-accent bg-accent",
        state === "missing" && "border-dashed border-text-muted bg-transparent",
        state === "disagrees" && "border-warning-signal",
        special && "outline-1 outline-offset-1 outline-text",
      )}
    />
  );
}

function Legend({ hasSpecial }: { hasSpecial: boolean }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted" aria-label="Legend">
      {(Object.keys(STATE_LABEL) as SessionStateName[]).map((k) => (
        <li key={k} className="flex items-center gap-1.5">
          <SessionSquare state={k} />
          {STATE_LABEL[k]}
        </li>
      ))}
      {hasSpecial ? (
        <li className="flex items-center gap-1.5">
          <SessionSquare state="observed" special />
          Half-day or closure confirmed by the exchange
        </li>
      ) : null}
    </ul>
  );
}

function CalendarBody({ cells, exchange, mic, label }: { cells: SessionCell[]; exchange: ExpectedInfo | null; mic: string | null; label: string }) {
  const layout = useMemo(() => stripLayout(cells), [cells]);
  const counts = useMemo(() => sessionCounts(cells), [cells]);
  const special = useMemo(() => new Map((exchange?.special ?? []).map((s) => [s.date, s.label])), [exchange]);
  const first = cells[0].session;
  const last = cells[cells.length - 1].session;
  const specialInWindow = (exchange?.special ?? []).filter((s) => s.date >= first && s.date <= last);
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto pb-1">
        <div
          role="img"
          aria-label={`${label}: ${counts.observed} of ${counts.total} sessions observed, ${counts.missing} missing, ${counts.disagrees} disagree, from ${first} to ${last}`}
          className="inline-grid gap-[3px]"
          style={{ gridTemplateColumns: `repeat(${layout.columns}, 0.875rem)`, gridTemplateRows: `repeat(${layout.rows}, 0.875rem)` }}
        >
          {layout.cells.map((c) => (
            <SessionSquare
              key={c.session}
              state={c.state}
              special={special.has(c.session)}
              style={{ gridColumn: c.column, gridRow: c.row }}
              title={`${formatDate(c.session)}: ${STATE_LABEL[c.state]}${special.has(c.session) ? `; ${special.get(c.session)}` : ""}`}
            />
          ))}
        </div>
      </div>
      <p className="num text-xs text-text-muted">
        {formatDate(first)} to {formatDate(last)}, oldest left, one column per week, Monday at the top. {counts.observed} observed, {counts.missing} missing,{" "}
        {counts.disagrees} disagree, of {counts.total} expected sessions.
      </p>
      <Legend hasSpecial={specialInWindow.length > 0} />
      {exchange ? (
        <p className="text-xs text-text-muted">
          Calendar: {exchange.basisLabel.toLowerCase()}
          {exchange.count !== null ? `, ${exchange.count} sessions in this window` : ""}.{" "}
          {specialInWindow.length > 0
            ? `Confirmed special days: ${specialInWindow.map((s) => `${formatDate(s.date)} (${s.label})`).join(", ")}.`
            : "No half-days or closures are confirmed in this window."}
        </p>
      ) : (
        <p className="text-xs text-text-muted">
          {mic ? `The ${mic} session calendar could not be loaded, so special days are not marked.` : "The exchange for this series is unknown, so special days are not marked."}
        </p>
      )}
      <SrTable
        caption={`${label}: state of each expected session`}
        columns={["Session", "State", "Special day"]}
        rows={cells.map((c) => [c.session, STATE_LABEL[c.state], special.get(c.session) ?? ""])}
      />
    </div>
  );
}

/** Loads the exchange's expected sessions for the strip's window, then renders the strip with them. */
function WithExchange({ mic, start, end, children }: { mic: string; start: string; end: string; children: (info: ExpectedInfo | null) => ReactNode }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/exchange-sessions/{mic}/expected", { params: { path: { mic }, query: { start, end } } }),
    [mic, start, end],
  );
  const { state } = useApiResource(fetcher);
  const info = useMemo(() => (state.kind === "ok" ? parseExpected(state.data) : null), [state]);
  return <>{children(info)}</>;
}

/** Week-aligned calendar of the latest expected sessions for one stored price series. */
export function SessionStrip({ cells, mic, label }: { cells: SessionCell[]; mic: string | null; label: string }) {
  if (cells.length === 0) {
    return <p className="text-xs text-text-muted">No session-by-session record for this series.</p>;
  }
  const first = cells[0].session;
  const last = cells[cells.length - 1].session;
  return mic ? (
    <WithExchange mic={mic} start={first} end={last}>
      {(info) => <CalendarBody cells={cells} exchange={info} mic={mic} label={label} />}
    </WithExchange>
  ) : (
    <CalendarBody cells={cells} exchange={null} mic={null} label={label} />
  );
}

// ---------------------------------------------------------------- corporate-action coverage

type Submit = { kind: "idle" } | { kind: "saving" } | { kind: "saved"; through: string } | { kind: "error"; message: string };

const FIELD_HINT = "mt-1 block text-xs text-text-muted";

/**
 * GET /holdings/{ticker}/corporate-actions/coverage lists each provider's recorded state; POST
 * records owner-verified coverage. Total return needs complete split and dividend history, so an
 * absent or unverified entry is shown as such and never as covered.
 */
export function CorporateActionCoveragePanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/corporate-actions/coverage", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const entries = useMemo(() => parseCoverage(raw ?? null), [raw]);

  const uid = useId();
  const [draft, setDraft] = useState<CoverageDraft>({ through: "", sourceUrl: "", reason: "" });
  const [errors, setErrors] = useState<CoverageErrors>({});
  const [submit, setSubmit] = useState<Submit>({ kind: "idle" });

  const set = (patch: Partial<CoverageDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const found = validateCoverage(draft, new Date().toISOString().slice(0, 10));
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSubmit({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().POST("/holdings/{ticker}/corporate-actions/coverage", {
        params: { path: { ticker } },
        body: { through: draft.through.trim(), source_url: draft.sourceUrl.trim(), reason: draft.reason.trim() },
      });
      if (r.response.ok) {
        setSubmit({ kind: "saved", through: draft.through.trim() });
        setDraft({ through: "", sourceUrl: "", reason: "" });
        reload();
      } else {
        const detail = r.error && typeof r.error === "object" && "detail" in r.error ? r.error.detail : "";
        setSubmit({
          kind: "error",
          message: `Coverage was not saved (HTTP ${r.response.status})${typeof detail === "string" && detail ? `: ${detail}` : "."}`,
        });
      }
    } catch (err) {
      setSubmit({ kind: "error", message: `Backend unreachable: ${err instanceof Error ? err.message : String(err)}` });
    }
  };

  const loading = state.kind === "loading" && raw === null;
  const failed = state.kind === "error" && raw === null;
  const describedBy = (k: keyof CoverageDraft) => (errors[k] ? `${uid}-${k}-err` : `${uid}-${k}-hint`);

  return (
    <Blueprint aria-labelledby="ca-coverage-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Data quality · corporate actions" title={<span id="ca-coverage-heading">Split and dividend coverage</span>} />
      <p className="max-w-[68ch] text-sm text-text-muted">
        Total return is only as complete as the split and dividend history behind it. Until some provider, or you, vouch that history is complete through a date,
        total return after that date is not shown as reliable.
      </p>

      {loading ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {failed && state.kind === "error" ? (
        <p className="text-sm text-text-muted">
          Coverage unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}
      {raw !== null && entries.length === 0 ? (
        <p className="border border-dashed border-border px-3 py-2 text-sm">No coverage recorded for this holding.</p>
      ) : null}

      {entries.length > 0 ? (
        <>
          <ul className="divide-y divide-border-soft border border-border">
            {entries.map((en) => (
              <li key={en.key} className="grid gap-x-4 gap-y-1 px-3 py-2.5 text-sm sm:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <span className="font-medium">{en.provider}</span>
                  <span
                    className={cn(
                      "ml-2 border px-1.5 py-0.5 text-xs",
                      en.ownerVerified ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted",
                    )}
                  >
                    {en.ownerVerified ? "Owner-verified" : "Provider-reported"}
                  </span>
                  {en.reason ? <span className="mt-1 block text-xs text-text-muted">{en.reason}</span> : null}
                </div>
                <div className="num text-left text-xs sm:text-right">
                  <span className="block text-sm">
                    {en.covered
                      ? en.latestPeriod
                        ? `Covered through ${formatDate(en.latestPeriod)}`
                        : "Covered, no end date given"
                      : `${en.stateLabel.charAt(0).toUpperCase()}${en.stateLabel.slice(1)}${en.latestPeriod ? `, latest period ${formatDate(en.latestPeriod)}` : ""}`}
                  </span>
                  <span className="text-text-muted">{en.checkedAt ? `Checked ${formatDate(en.checkedAt)}` : "Check date not recorded"}</span>
                </div>
              </li>
            ))}
          </ul>
          <SrTable
            caption="Corporate-action coverage by provider"
            columns={["Provider", "Source of claim", "State", "Latest period", "Checked"]}
            rows={entries.map((en) => [
              en.provider,
              en.ownerVerified ? "Owner-verified" : "Provider-reported",
              en.stateLabel,
              en.latestPeriod ?? "None",
              en.checkedAt ?? "Not recorded",
            ])}
          />
        </>
      ) : null}

      <form onSubmit={onSubmit} noValidate className="space-y-3 border-t border-border pt-4" aria-labelledby={`${uid}-form`}>
        <h3 id={`${uid}-form`} className="text-sm font-semibold">
          Confirm coverage yourself
        </h3>
        <div className="grid gap-3 md:grid-cols-[12rem_minmax(0,1fr)]">
          <label className="block text-sm">
            <span className="font-medium">Complete through</span>
            <Input
              type="date"
              value={draft.through}
              onChange={(e) => set({ through: e.target.value })}
              aria-invalid={errors.through ? true : undefined}
              aria-describedby={describedBy("through")}
              className="mt-1"
            />
            {errors.through ? (
              <span id={`${uid}-through-err`} role="alert" className={cn(FIELD_HINT, "text-warning-signal")}>
                {errors.through}
              </span>
            ) : (
              <span id={`${uid}-through-hint`} className={FIELD_HINT}>
                Last date with every split and dividend recorded.
              </span>
            )}
          </label>
          <label className="block text-sm">
            <span className="font-medium">Source you checked</span>
            <Input
              type="text"
              inputMode="url"
              value={draft.sourceUrl}
              onChange={(e) => set({ sourceUrl: e.target.value })}
              placeholder="https://…"
              aria-invalid={errors.sourceUrl ? true : undefined}
              aria-describedby={describedBy("sourceUrl")}
              className="mt-1"
            />
            {errors.sourceUrl ? (
              <span id={`${uid}-sourceUrl-err`} role="alert" className={cn(FIELD_HINT, "text-warning-signal")}>
                {errors.sourceUrl}
              </span>
            ) : (
              <span id={`${uid}-sourceUrl-hint`} className={FIELD_HINT}>
                Link to the issuer or exchange page; at least {COVERAGE_LIMITS.sourceUrlMin} characters.
              </span>
            )}
          </label>
        </div>
        <label className="block text-sm">
          <span className="font-medium">What you verified</span>
          <Input
            type="text"
            value={draft.reason}
            onChange={(e) => set({ reason: e.target.value })}
            aria-invalid={errors.reason ? true : undefined}
            aria-describedby={describedBy("reason")}
            className="mt-1"
          />
          {errors.reason ? (
            <span id={`${uid}-reason-err`} role="alert" className={cn(FIELD_HINT, "text-warning-signal")}>
              {errors.reason}
            </span>
          ) : (
            <span id={`${uid}-reason-hint`} className={FIELD_HINT}>
              For example, which actions you matched against the source; at least {COVERAGE_LIMITS.reasonMin} characters.
            </span>
          )}
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={submit.kind === "saving"}>
            {submit.kind === "saving" ? "Saving…" : "Confirm coverage"}
          </Button>
          <p role="status" className="text-sm">
            {submit.kind === "saved" ? `Coverage confirmed through ${formatDate(submit.through)}.` : null}
            {submit.kind === "error" ? <span className="text-warning-signal">{submit.message}</span> : null}
          </p>
        </div>
      </form>
    </Blueprint>
  );
}
