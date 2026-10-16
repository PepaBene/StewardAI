"use client";

import { LoaderCircleIcon, SparklesIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EntityName } from "@/components/entity-name";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding } from "@/lib/api/client";
import { cn } from "@/lib/utils";

const isFund = (h: Holding) => /etf|fund/i.test(h.security_type ?? "");

/**
 * With no thesis written yet, Theses opens on this: pick the companies you hold and the app
 * drafts a thesis for each (one workup job per company), which you then edit and keep.
 */
export function GuidedStart() {
  const [holdings, setHoldings] = useState<Holding[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ queued: number; failed: string[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then(({ data }) => {
        if (cancelled) return;
        const companies = (data?.holdings ?? []).filter((h) => !isFund(h));
        setHoldings(companies);
        // Start with the five largest positions ticked: the theses that matter most.
        const top = [...companies].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0)).slice(0, 5);
        setPicked(new Set(top.map((h) => h.ticker)));
      })
      .catch(() => !cancelled && setHoldings([]));
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (t: string) =>
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  async function start() {
    setPending(true);
    const api = getBrowserApiClient();
    const failed: string[] = [];
    let queued = 0;
    for (const ticker of picked) {
      try {
        const { data } = await api.POST("/jobs/thesis", { body: { ticker, mode: "held" } });
        if (data) queued += 1;
        else failed.push(ticker);
      } catch {
        failed.push(ticker);
      }
    }
    setResult({ queued, failed });
    setPending(false);
  }

  return (
    <section className="blueprint px-5 py-6 md:px-8" aria-labelledby="guided-start-title">
      <h2 id="guided-start-title" className="font-heading text-[24px] leading-tight font-semibold">
        Write down why you own each company
      </h2>
      <p className="mt-1.5 max-w-prose text-sm text-text-muted">
        A thesis is the reason you hold a company and the few figures that must stay true. Pick the companies to start
        with; the app drafts a thesis for each from its filings, and you edit what it got wrong.
      </p>

      {holdings === null ? (
        <p className="mt-5 flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading your holdings…
        </p>
      ) : holdings.length === 0 ? (
        <p className="mt-5 text-sm text-text-muted">
          No company holdings yet. You can still write a thesis by hand in{" "}
          <Link href="/config/theses" className="text-accent-ink hover:underline">
            Settings › Theses
          </Link>
          .
        </p>
      ) : (
        <>
          <fieldset className="mt-5">
            <legend className="sr-only">Companies to draft a thesis for</legend>
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {holdings.map((h) => {
                const on = picked.has(h.ticker);
                return (
                  <li key={h.ticker}>
                    <label
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-control px-3 py-2.5 text-sm transition-colors",
                        on ? "bg-accent-soft text-text" : "bg-surface-muted text-text-muted hover:text-text",
                      )}
                    >
                      <input type="checkbox" checked={on} onChange={() => toggle(h.ticker)} className="size-4 accent-accent" />
                      <span className="min-w-0 flex-1 truncate">
                        <EntityName id={h.ticker} name={h.name} chip={false} />
                      </span>
                      {h.weight != null ? <span className="num text-xs text-text-faint">{(h.weight * 100).toFixed(1)}%</span> : null}
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Button onClick={() => void start()} disabled={pending || picked.size === 0 || result !== null}>
              {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SparklesIcon aria-hidden="true" />}
              Draft {picked.size} {picked.size === 1 ? "thesis" : "theses"}
            </Button>
            <Link href="/config/theses" className="text-sm text-text-muted hover:text-text">
              Write one by hand instead
            </Link>
          </div>
        </>
      )}

      {result ? (
        <div className="mt-4" role="status">
          <AlertMessage tone="info" title={`${result.queued} ${result.queued === 1 ? "draft" : "drafts"} started`}>
            Each draft takes a few minutes. Follow them on the{" "}
            <Link href="/thesis" className="text-accent-ink hover:underline">
              thesis workup page
            </Link>
            .{result.failed.length > 0 ? ` Could not start: ${result.failed.join(", ")}.` : ""}
          </AlertMessage>
        </div>
      ) : null}
    </section>
  );
}
