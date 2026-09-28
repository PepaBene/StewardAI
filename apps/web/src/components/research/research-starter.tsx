"use client";

import { FileTextIcon, LoaderCircleIcon, PlayIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type JobSummary, type ResearchSummary } from "@/lib/api/client";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { jobHref } from "@/lib/jobs/job-detail";
import { normaliseTicker, profileOptions, researchHref, type ProfileOption } from "@/lib/research/report";

const SELECT =
  "h-9 w-full border border-border bg-surface-raised px-3 py-1 text-base text-text shadow-xs outline-none md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

/** The research profiles to offer: the saved ones, or the backend defaults on a 404. */
export function useResearchProfiles(): { options: ProfileOption[]; defaultId: string; saved: boolean | null } {
  const [state, setState] = useState<{ options: ProfileOption[]; defaultId: string; saved: boolean | null }>({
    ...profileOptions(null),
    saved: null,
  });
  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/config/research-profiles")
      .then(({ data }) => {
        if (!cancelled) setState({ ...profileOptions(data?.body ?? null), saved: Boolean(data) });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}

/**
 * Start a research run: a ticker (unless fixed, as on a holding page) and a profile, then
 * `POST /jobs/research`. Follows the queued job and, once it has succeeded, links the new
 * report (found by its job id).
 */
export function ResearchStarter({
  ticker: fixedTicker,
  initialTicker = "",
  submitLabel = "Start research",
  onFinished,
}: {
  ticker?: string;
  initialTicker?: string;
  submitLabel?: string;
  onFinished?: () => void;
}) {
  const id = useId();
  const profiles = useResearchProfiles();
  const [ticker, setTicker] = useState(initialTicker);
  const [profile, setProfile] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<{ job: JobSummary; ticker: string } | null>(null);
  const [report, setReport] = useState<ResearchSummary | null>(null);
  const watched = useJobStatus(queued?.job ?? null);
  const chosen = profile ?? profiles.defaultId;
  const done = watched?.status === "succeeded";
  const finishedRef = useRef(onFinished);
  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  // Once the run has succeeded, find the report it wrote.
  useEffect(() => {
    if (!done || !queued) return;
    let cancelled = false;
    getBrowserApiClient()
      .GET("/research", { params: { query: { ticker: queued.ticker, limit: 20 } } })
      .then(({ data }) => {
        if (cancelled || !data) return;
        setReport(data.find((r) => r.job_id === queued.job.id) ?? null);
        finishedRef.current?.();
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [done, queued]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const t = normaliseTicker(fixedTicker ?? ticker);
    if (!t) {
      setError("Type a ticker: letters, digits, dots, dashes or underscores (up to 32), e.g. AAPL_US_EQ.");
      return;
    }
    setPending(true);
    setError(null);
    setReport(null);
    try {
      const { data, response, error: apiError } = await getBrowserApiClient().POST("/jobs/research", {
        body: { ticker: t, profile: chosen },
      });
      if (data) setQueued({ job: data, ticker: t });
      else {
        const detail = apiError && typeof apiError === "object" && "detail" in apiError ? String((apiError as { detail: unknown }).detail) : "";
        setError(`Not queued (HTTP ${response.status})${detail && !detail.startsWith("[") ? `: ${detail}` : "."}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2">
      <form onSubmit={(e) => void submit(e)} className="flex flex-wrap items-end gap-2">
        {fixedTicker ? null : (
          <div className="w-48 space-y-1">
            <Label htmlFor={`${id}-ticker`}>Ticker</Label>
            <Input
              id={`${id}-ticker`}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="AAPL_US_EQ"
              spellCheck={false}
              autoCapitalize="characters"
              maxLength={32}
              aria-describedby={`${id}-ticker-hint`}
            />
          </div>
        )}
        <div className="w-56 space-y-1">
          <Label htmlFor={`${id}-profile`}>Profile</Label>
          <select id={`${id}-profile`} value={chosen} onChange={(e) => setProfile(e.target.value)} className={SELECT}>
            {profiles.options.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.id}){p.id === profiles.defaultId ? " · default" : ""}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" size="sm" disabled={pending} className="mb-0.5">
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          {submitLabel}
        </Button>
      </form>
      {fixedTicker ? null : (
        <p id={`${id}-ticker-hint`} className="text-xs text-text-muted">
          Any ticker, owned or not; a Trading212 ticker gives the richest data.{" "}
          {profiles.saved === false ? "No profiles saved yet: the defaults (standard, quick) apply." : null}{" "}
          <Link href="/config/research-profiles" className="text-accent-ink hover:underline">
            Edit profiles
          </Link>
        </p>
      )}
      {error ? <AlertMessage title="Research not started">{error}</AlertMessage> : null}
      {queued && watched ? (
        <div role="status" className="flex flex-wrap items-center gap-2 border border-border bg-surface-muted p-2 text-sm">
          <span>
            Research on <span className="font-mono">{queued.ticker}</span> ({chosen}):
          </span>
          <JobStatusBadge status={watched.status} />
          <Link href={jobHref(queued.job.id)} className="text-accent-ink hover:underline">
            job #{queued.job.id}
          </Link>
          {watched.status === "failed" && watched.error ? <span className="text-text">{watched.error}</span> : null}
          {watched.gaveUp ? <span className="text-text-muted">Still not finished; follow the job page.</span> : null}
          {report ? (
            <Link href={researchHref(report.id)} className="inline-flex items-center gap-1 font-medium text-accent-ink hover:underline">
              <FileTextIcon aria-hidden="true" className="size-4" />
              Open the new report
            </Link>
          ) : done ? (
            <span className="text-text-muted">Finished; the report is in the list.</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
