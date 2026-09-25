import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Config" };

const DOMAINS = [
  {
    href: "/config/theses",
    title: "Theses",
    body: "Why each holding is owned, the KPIs that must hold, and what would end it.",
  },
  {
    href: "/config/holding-profiles",
    title: "Holding profiles",
    body: "Where each company publishes results, its fiscal year end, and what news is relevant to it.",
  },
  {
    href: "/config/news-sources",
    title: "News sources",
    body: "The feeds the news check reads, and how far each outlet is trusted.",
  },
  {
    href: "/config/event-rules",
    title: "Event rules",
    body: "The severity of each kind of event, per-holding overrides and adjustments.",
  },
  {
    href: "/config/notifications",
    title: "Notifications",
    body: "Which confirmed events reach you, on which channel; quiet hours, caps, digests. Send a test alert.",
  },
] as const;

const LATER = [
  { title: "Strategy", note: "PUT /config/strategy (editor in a later phase)" },
  { title: "Jobs", note: "PUT /config/jobs: schedules, models, step caps" },
  { title: "Verification", note: "PUT /config/verification: sanity bounds, tolerances" },
  { title: "Research profiles, budget", note: "Phases 5 and 6" },
] as const;

export default function ConfigPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Config</h1>
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Every preference is versioned config: each save is a new version, validated by the
          backend before it is stored, and used from the next run with no deploy.
        </p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2">
        {DOMAINS.map((d) => (
          <li key={d.href}>
            <Link
              href={d.href}
              className="block h-full rounded-lg border border-border bg-surface-raised p-4 transition-colors hover:border-accent"
            >
              <span className="font-medium text-text">{d.title}</span>
              <span className="mt-1 block text-sm text-text-muted">{d.body}</span>
            </Link>
          </li>
        ))}
      </ul>
      <div>
        <h2 className="text-sm font-medium text-text-muted">Not in the editor yet</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {LATER.map((d) => (
            <li key={d.title}>
              <span className="font-medium">{d.title}</span>{" "}
              <span className="text-text-muted">— {d.note}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
