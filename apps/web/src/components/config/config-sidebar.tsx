"use client";

import {
  BellIcon,
  CalendarClockIcon,
  CalendarIcon,
  FlaskConicalIcon,
  GaugeIcon,
  GitBranchIcon,
  HistoryIcon,
  IdCardIcon,
  LightbulbIcon,
  ListChecksIcon,
  MessageSquareTextIcon,
  RssIcon,
  ShieldCheckIcon,
  TargetIcon,
  WalletIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { activeNavKey, CONFIG_NAV, navCounts, type ConfigNavKey, type NavCount } from "@/lib/config/sidebar";
import { DESTINATIONS } from "@/lib/nav";
import { cn } from "@/lib/utils";

const ICONS: Record<ConfigNavKey, LucideIcon> = {
  strategy: TargetIcon,
  theses: LightbulbIcon,
  "holding-profiles": IdCardIcon,
  "event-rules": GitBranchIcon,
  "news-sources": RssIcon,
  notifications: BellIcon,
  "research-profiles": FlaskConicalIcon,
  jobs: CalendarClockIcon,
  budget: WalletIcon,
  prompts: MessageSquareTextIcon,
  verification: ShieldCheckIcon,
  checks: ListChecksIcon,
  calendar: CalendarIcon,
  autonomy: GaugeIcon,
  history: HistoryIcon,
};

/**
 * The config domains, with a short count each (read once from GET /config/export, the
 * active version of every domain in one request). A column at lg, a scrolling strip below.
 */
export function ConfigSidebar() {
  const pathname = usePathname();
  const active = activeNavKey(pathname);
  const [counts, setCounts] = useState<Partial<Record<ConfigNavKey, NavCount>>>({});

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/config/export")
      .then(({ data }) => {
        if (!cancelled && data) setCounts(navCounts(data));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // Counts change on save; re-read when the owner moves between config pages.
  }, [pathname]);

  const byHref = new Map(CONFIG_NAV.map((item) => [item.href, item]));

  return (
    <nav aria-label="Settings" className="lg:sticky lg:top-20">
      <div className="hidden px-3 pb-3 font-heading text-[20px] font-semibold lg:block">Settings</div>
      {/* Desktop: grouped menu (plan §3.2 Settings). Mobile: one scrolling chip row. */}
      <div className="hidden space-y-5 lg:block">
        {SETTINGS_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="kicker px-3 pb-1.5">{group.label}</div>
            <ul className="space-y-0.5">
              {group.tabs.map((tab) => {
                const item = byHref.get(tab.href);
                if (!item) return null;
                const Icon = ICONS[item.key];
                const current = item.key === active;
                const count = counts[item.key];
                return (
                  <li key={item.key}>
                    <Link
                      href={item.href}
                      aria-current={current ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition-colors",
                        current ? "bg-surface-raised font-medium text-text" : "text-text-muted hover:bg-surface-muted hover:text-text",
                      )}
                    >
                      <Icon aria-hidden="true" className="size-[15px] shrink-0" />
                      <span className="flex-1">{tab.label}</span>
                      {count ? (
                        <span title={count.title} className="num text-xs text-text-faint">
                          {count.text}
                          <span className="sr-only"> ({count.title})</span>
                        </span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
      <ul className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] lg:hidden">
        {SETTINGS_GROUPS.flatMap((g) => g.tabs).map((tab) => {
          const item = byHref.get(tab.href);
          if (!item) return null;
          const current = item.key === active;
          return (
            <li key={item.key} className="shrink-0">
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "inline-flex h-8 items-center rounded-full px-3 text-[13px] font-medium",
                  current ? "bg-accent text-accent-foreground" : "bg-surface-muted text-text-muted",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

const SETTINGS_GROUPS = DESTINATIONS.find((d) => d.id === "settings")?.groups ?? [];
