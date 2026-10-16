import { CalendarDaysIcon, HistoryIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import type { ConfigDomainSlug } from "@/lib/api/client";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
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
    body: "When each scheduled job runs, whether it runs, its time zone, the models and caps of the jobs that call a model (including chat and the monthly review), reference data, price benchmarks, the holiday calendar and 13F ownership with tracked managers.",
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
  {
    slug: "research-profiles",
    href: "/config/research-profiles",
    title: "Research profiles",
    body: "The shapes a research run can take: sections and their order, depth, model, effort, step and search caps, source tiers, peers, valuation methods and language.",
  },
  {
    slug: "prompts",
    href: "/config/prompts",
    title: "Prompts",
    body: "The instructions each AI step sends: extraction, news classification, digests, impact notes, research, the monthly review, chat and the claim verifier. Override any of them or reset to the built-in text.",
  },
  {
    slug: "budget",
    href: "/config/budget",
    title: "Budget",
    body: "Model prices, the monthly AI spend cap, per-job caps and what happens at the cap; this month's spend by job and model.",
  },
];

export default function ConfigPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description={
          <p className="max-w-prose">
            Every preference is versioned config: each save is a new version, validated by the backend before it is
            stored, and used from the next run with no deploy. Any version can be compared with another or rolled back.
          </p>
        }
        aside={
          <Button asChild variant="outline" size="sm">
            <Link href="/config/history">
              <HistoryIcon aria-hidden="true" />
              History &amp; export
            </Link>
          </Button>
        }
      />
      <ul className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {DOMAINS.map((d) => (
          <li key={d.href} className="flex h-full flex-col rounded-card bg-surface-muted transition-colors hover:bg-surface">
            <Link href={d.href} className="block flex-1 rounded-card p-5 pb-2 outline-none focus-visible:ring-2 focus-visible:ring-focus/30">
              <span className="font-heading text-lg leading-tight font-semibold text-text">{d.title}</span>
              <span className="mt-1 block text-sm text-text-muted">{d.body}</span>
            </Link>
            <Link
              href={historyHref(d.slug)}
              className="inline-flex items-center gap-1 self-start px-5 pb-4 text-xs text-text-muted hover:text-text hover:underline"
            >
              <HistoryIcon aria-hidden="true" className="size-3.5" />
              {d.title} history
            </Link>
          </li>
        ))}
        <li className="flex h-full flex-col rounded-card bg-surface-muted transition-colors hover:bg-surface">
          <Link href="/config/calendar" className="flex flex-1 items-start gap-3 rounded-card p-5 outline-none focus-visible:ring-2 focus-visible:ring-focus/30">
            <CalendarDaysIcon aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-text-muted" />
            <span>
              <span className="font-heading text-lg leading-tight font-semibold text-text">Exchange calendar</span>
              <span className="mt-1 block text-sm text-text-muted">
                Confirm which public holidays close each holding&rsquo;s exchange. Confirmed closures are saved to the
                Jobs config.
              </span>
            </span>
          </Link>
        </li>
      </ul>
      <ConfigTransfer />
    </div>
  );
}
