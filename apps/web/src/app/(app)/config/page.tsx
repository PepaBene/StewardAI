import { CalendarDaysIcon, HistoryIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import type { ConfigDomainSlug } from "@/lib/api/client";
import { historyHref } from "@/lib/config/domains";

import { ConfigTransfer } from "./config-transfer";

export const metadata: Metadata = { title: "Config" };

const DOMAINS: { slug: ConfigDomainSlug; href: string; title: string; body: string }[] = [
  {
    slug: "strategy",
    href: "/config/strategy",
    title: "Strategy",
    body: "Base currency, categories, position, category and cash targets with bands, hard limits and rules. Drift is computed against it.",
  },
  {
    slug: "jobs",
    href: "/config/jobs",
    title: "Jobs",
    body: "When each scheduled job runs, whether it runs, its time zone, the models and caps of the jobs that call Claude, reference data, price benchmarks and the holiday calendar.",
  },
  {
    slug: "theses",
    href: "/config/theses",
    title: "Theses",
    body: "Why each holding is owned, the KPIs that must hold, and what would end it.",
  },
  {
    slug: "holding-profiles",
    href: "/config/holding-profiles",
    title: "Holding profiles",
    body: "Where each company publishes results, its fiscal year end, its Stooq price symbol, and what news is relevant to it.",
  },
  {
    slug: "news-sources",
    href: "/config/news-sources",
    title: "News sources",
    body: "The feeds the news check reads, and how far each outlet is trusted.",
  },
  {
    slug: "event-rules",
    href: "/config/event-rules",
    title: "Event rules",
    body: "The severity of each kind of event, per-holding overrides and adjustments.",
  },
  {
    slug: "notifications",
    href: "/config/notifications",
    title: "Notifications",
    body: "Which confirmed events reach you, on which channel; quiet hours, caps, digests. Send a test alert.",
  },
  {
    slug: "verification",
    href: "/config/verification",
    title: "Verification",
    body: "Sanity bounds and tolerances for extracted figures, the corroboration rules, source trust from your feedback, and the claim verifier.",
  },
];

const LATER: { title: string; note: string; slug?: ConfigDomainSlug }[] = [
  { title: "Research profiles", note: "Sections, depth, model and budgets per research run; phase 5" },
  { title: "Budget", note: "Monthly and per-job caps and what happens at the cap; editor in phase 6", slug: "budget" },
];

export default function ConfigPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Config</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Every preference is versioned config: each save is a new version, validated by the
            backend before it is stored, and used from the next run with no deploy. Any version
            can be compared with another or rolled back.
          </p>
        </div>
        <Link href="/config/history" className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
          <HistoryIcon aria-hidden="true" className="size-4" />
          Version history
        </Link>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2">
        {DOMAINS.map((d) => (
          <li key={d.href} className="flex h-full flex-col rounded-lg border border-border bg-surface-raised transition-colors hover:border-accent">
            <Link href={d.href} className="block flex-1 rounded-t-lg p-4 pb-2">
              <span className="font-medium text-text">{d.title}</span>
              <span className="mt-1 block text-sm text-text-muted">{d.body}</span>
            </Link>
            <Link
              href={historyHref(d.slug)}
              className="inline-flex items-center gap-1 self-start rounded-sm px-4 pb-3 text-xs text-text-muted hover:text-text hover:underline"
            >
              <HistoryIcon aria-hidden="true" className="size-3.5" />
              {d.title} history
            </Link>
          </li>
        ))}
      </ul>
      <Link
        href="/config/calendar"
        className="flex items-start gap-3 rounded-lg border border-border bg-surface-raised p-4 transition-colors hover:border-accent"
      >
        <CalendarDaysIcon aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-text-muted" />
        <span>
          <span className="font-medium text-text">Exchange calendar</span>
          <span className="mt-1 block text-sm text-text-muted">
            Confirm which public holidays close each holding&rsquo;s exchange. Confirmed closures are saved to the Jobs
            config.
          </span>
        </span>
      </Link>
      <div>
        <h2 className="text-sm font-medium text-text-muted">Not in the editor yet</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {LATER.map((d) => (
            <li key={d.title}>
              <span className="font-medium">{d.title}</span> <span className="text-text-muted">— {d.note}</span>
              {d.slug ? (
                <>
                  {" "}
                  <Link href={historyHref(d.slug)} className="text-accent hover:underline">
                    history
                  </Link>
                </>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
      <ConfigTransfer />
    </div>
  );
}
