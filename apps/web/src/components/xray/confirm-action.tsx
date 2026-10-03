"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { jobHref } from "@/lib/jobs/job-detail";

/**
 * A button that never acts on the first click: it opens an inline confirmation that says
 * what will happen, and only the second click runs `run`. `run` returns a short result line
 * (or throws, which is shown as the error).
 */
export function ConfirmAction({
  label,
  confirmTitle,
  confirmBody,
  confirmLabel = "Confirm",
  run,
  variant = "outline",
}: {
  label: ReactNode;
  confirmTitle: string;
  confirmBody: ReactNode;
  confirmLabel?: string;
  run: () => Promise<ReactNode>;
  variant?: "outline" | "default";
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReactNode>(null);
  const [error, setError] = useState("");
  const id = useId();

  async function go() {
    setBusy(true);
    setError("");
    try {
      setResult(await run());
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant={variant} aria-expanded={open} aria-controls={id} onClick={() => { setOpen((o) => !o); setResult(null); setError(""); }}>
          {label}
        </Button>
        {result ? (
          <span role="status" className="text-xs text-text-muted">
            {result}
          </span>
        ) : null}
      </div>
      {open ? (
        <div id={id} role="group" aria-label={confirmTitle} className="flex max-w-prose flex-col gap-2 border border-border border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-2.5 text-sm">
          <p className="font-medium text-text">{confirmTitle}</p>
          <div className="text-xs text-text-muted">{confirmBody}</div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} onClick={() => void go()}>
              {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
              {confirmLabel}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
          {error ? (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export type OwnerJob = "ownership-13f" | "nport-holdings" | "etf-look-through" | "insider-trades" | "control-stakes";

const JOB_COPY: Record<OwnerJob, { label: string; body: string }> = {
  "ownership-13f": { label: "Load SEC 13F data", body: "Queues the Form 13F load for the portfolio's US holdings and tracked managers. It reads public SEC data sets and may take a while." },
  "nport-holdings": { label: "Load SEC N-PORT holdings", body: "Queues the N-PORT load for the portfolio's US funds that have no issuer holdings snapshot." },
  "etf-look-through": { label: "Fetch fund holdings from issuers", body: "Queues a fetch of the configured funds' holdings from iShares and Vanguard. Funds whose stored as-of date is already current are skipped." },
  "insider-trades": { label: "Load insider filings", body: "Queues the load of Forms 3/4/5 for portfolio issuers from SEC EDGAR." },
  "control-stakes": { label: "Load Schedule 13D/G filings", body: "Queues the load of 13D/G filings for held issuers and tracked managers." },
};

async function post(job: OwnerJob) {
  const api = getBrowserApiClient();
  switch (job) {
    case "ownership-13f":
      return api.POST("/jobs/ownership-13f");
    case "nport-holdings":
      return api.POST("/jobs/nport-holdings");
    case "etf-look-through":
      return api.POST("/jobs/etf-look-through");
    case "insider-trades":
      return api.POST("/jobs/insider-trades");
    case "control-stakes":
      return api.POST("/jobs/control-stakes");
  }
}

/** Owner-triggered collection job with confirmation. Nothing here runs on page load. */
export function JobTrigger({ job }: { job: OwnerJob }) {
  const copy = JOB_COPY[job];
  return (
    <ConfirmAction
      label={copy.label}
      confirmTitle={`${copy.label}?`}
      confirmBody={copy.body}
      confirmLabel="Queue job"
      run={async () => {
        const result = await post(job);
        if (!result.data) throw new Error(`The job could not be queued (HTTP ${result.response.status}).`);
        const id = result.data.id;
        return (
          <Link href={jobHref(id)} className="text-accent-ink hover:underline">
            Queued as job #{id}
          </Link>
        );
      }}
    />
  );
}
