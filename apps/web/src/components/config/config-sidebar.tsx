"use client";

import {
  BellIcon,
  CalendarClockIcon,
  CalendarIcon,
  FlaskConicalIcon,
  GitBranchIcon,
  HistoryIcon,
  IdCardIcon,
  LightbulbIcon,
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
  calendar: CalendarIcon,
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

  return (
    <nav aria-label="Configuration" className="lg:sticky lg:top-0">
      <Link
        href="/config"
        aria-current={pathname === "/config" ? "page" : undefined}
        className={cn(
          "hidden px-6 pb-2.5 text-[11px] font-semibold tracking-[0.14em] text-text-muted uppercase hover:text-text lg:block",
          pathname === "/config" && "text-accent-ink",
        )}
      >
        Configuration
      </Link>
      <ul className="-mx-4 flex gap-0.5 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
        {CONFIG_NAV.map((item) => {
          const Icon = ICONS[item.key];
          const current = item.key === active;
          const count = counts[item.key];
          return (
            <li key={item.key} className="shrink-0">
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap transition-colors lg:border-b-0 lg:border-l-2 lg:px-6",
                  current
                    ? "border-accent bg-accent-soft font-semibold text-accent-ink"
                    : "border-transparent text-text hover:bg-border-soft",
                )}
              >
                <Icon aria-hidden="true" className="size-[15px] shrink-0" />
                <span className="flex-1">{item.label}</span>
                {count ? (
                  <span title={count.title} className="num font-mono text-[11px] font-normal text-text-faint">
                    {count.text}
                    <span className="sr-only"> ({count.title})</span>
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
