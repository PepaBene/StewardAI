"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsTabs } from "@/components/news/news-tabs";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatDateTime } from "@/lib/format";
import { minutesLabel, usdLabel } from "@/lib/news/ops";

type OpsReport = components["schemas"]["OpsReport"];
type Candidate = components["schemas"]["CandidateCase"];

const DAYS = [7, 30, 90] as const;

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; report: OpsReport; marks: Candidate[] | null };

/** What the news jobs cost, how fast alerts arrive, how many were indirect, and what to review. */
export function OpsView() {
  const [days, setDays] = useState<(typeof DAYS)[number]>(30);
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    const api = getBrowserApiClient();
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    Promise.all([
      api.GET("/news/ops", { params: { query: { days } } }),
      // A failed marks request must not read as "no marks yet": null means it failed.
      api.GET("/news/marks/candidates").catch(() => null),
    ])
      .then(([ops, marks]) => {
        if (cancelled) return;
        if (ops.data) setLoad({ kind: "ok", report: ops.data, marks: marks?.data ?? null });
        else setLoad({ kind: "error", message: `The API answered HTTP ${ops.response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Review"
        description={
          <span className="block max-w-prose">
            What the news jobs cost, how long alerts took, how many came from indirect links, and the stories worth a second look.
            Spend limits are under{" "}
            <Link href="/config/budget" className="text-accent-ink hover:underline">
              Config, Budget
            </Link>
            .
          </span>
        }
      />
      <NewsTabs active="ops" />
      <div role="group" aria-label="Window" className="flex gap-2 text-sm">
        {DAYS.map((d) => (
          <button
            key={d}
            aria-pressed={d === days}
            onClick={() => setDays(d)}
            className={d === days ? "rounded-md border border-accent bg-accent-soft px-3 py-1 font-medium" : "rounded-md border border-border px-3 py-1 text-text-muted hover:text-text"}
          >
            {d} days
          </button>
        ))}
      </div>
      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the review">{load.message}</AlertMessage> : null}
      {load.kind === "ok" ? <Report report={load.report} marks={load.marks} /> : null}
    </div>
  );
}

function Report({ report, marks }: { report: OpsReport; marks: Candidate[] | null }) {
  const { latency, volume } = report;
  return (
    <>
      <section aria-labelledby="cost-heading" className="space-y-2">
        <h2 id="cost-heading" className="text-[20px] leading-tight font-semibold">
          Cost
        </h2>
        <p className="num text-sm">
          {usdLabel(report.cost_usd)} in {report.days} days.{" "}
          {report.cost_per_useful_event === null || report.cost_per_useful_event === undefined
            ? "No story is marked useful or alerted yet, so there is no cost per useful event."
            : `${usdLabel(report.cost_per_useful_event)} per useful event (${report.useful_events}).`}
        </p>
        {report.by_job.length > 0 ? (
          <table className="w-full max-w-xl text-left text-sm">
            <caption className="sr-only">News job spend</caption>
            <thead className="text-xs text-text-muted">
              <tr className="border-b border-border">
                <th scope="col" className="py-2 pr-3 font-normal">Job</th>
                <th scope="col" className="num pr-3 font-normal">Runs</th>
                <th scope="col" className="num font-normal">Spend</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {report.by_job.map((j) => (
                <tr key={j.job_type}>
                  <td className="py-2 pr-3">{j.job_type}</td>
                  <td className="num pr-3">{j.runs}</td>
                  <td className="num">{usdLabel(j.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      <section aria-labelledby="alerts-heading" className="space-y-1">
        <h2 id="alerts-heading" className="text-[20px] leading-tight font-semibold">
          Alerts
        </h2>
        <p className="num text-sm">
          {volume.total} sent, {volume.per_day} a day. {volume.indirect} ({volume.indirect_per_day} a day) reached the holding
          only through a competitor, supplier, customer or watch topic.
        </p>
        <p className="num text-sm">
          {latency.alerts === 0
            ? "No alert was sent in this window, so there is no latency."
            : `From the first article to the alert: median ${minutesLabel(latency.p50_minutes)}, slowest 1 in 20 ${minutesLabel(latency.p95_minutes)}.`}
        </p>
      </section>

      <section aria-labelledby="incidents-heading" className="space-y-2">
        <h2 id="incidents-heading" className="text-[20px] leading-tight font-semibold">
          Worth a second look
        </h2>
        {report.incidents.length === 0 ? (
          <EmptyState title="Nothing to review">No severe story went unalerted and no alert was marked not useful.</EmptyState>
        ) : (
          <ul className="m-0 list-none divide-y divide-border p-0 text-sm">
            {report.incidents.map((i) => (
              <li key={`${i.kind}${i.story_id}`} className="space-y-0.5 py-2">
                <Link href={`/news/stories/${i.story_id}`} className="font-medium text-accent-ink hover:underline">
                  {i.headline}
                </Link>
                <p className="text-xs text-text-muted">
                  {i.ticker ? <span className="font-mono">{i.ticker}</span> : null} {formatDateTime(i.first_seen)}. {i.reason}.
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="marks-heading" className="space-y-2">
        <h2 id="marks-heading" className="text-[20px] leading-tight font-semibold">
          Your marks as test cases
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          Items you marked wrong. Each needs the right answer from you before it can join the benchmark; nothing here changes
          a score or a model.
        </p>
        {marks === null ? (
          <AlertMessage title="Could not load your marks">The marks request failed, so this list is unknown, not empty. Reload to try again.</AlertMessage>
        ) : marks.length === 0 ? (
          <p className="text-sm text-text-muted">No wrong-item marks yet.</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-border p-0 text-sm">
            {marks.map((m) => (
              <li key={m.id} className="space-y-0.5 py-2">
                <p>{m.headline}</p>
                <p className="text-xs text-text-muted">
                  Marked {m.mark.replace("_", " ")} {formatDateTime(m.marked_at)}
                  {m.candidates?.length ? `, linked to ${m.candidates.join(", ")}` : ""}
                  {m.note ? `. ${m.note}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
