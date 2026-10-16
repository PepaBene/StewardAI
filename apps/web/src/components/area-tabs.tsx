"use client";

import { ChevronDownIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { type NavTab, resolveNav } from "@/lib/nav";
import { cn } from "@/lib/utils";

/** At most this many tabs show in a row on desktop; the rest go under "More". */
const VISIBLE = 7;

/**
 * The tabs of the current destination (TabBar, visual spec B40): text tabs with an accent
 * underline on desktop, overflow under "More"; a horizontally scrolling chip row below md.
 * Not shown for Settings (grouped side menu), the Inbox (one page) or inside a company or
 * fund page (those have their own section navigation).
 */
export function AreaTabs() {
  const pathname = usePathname();
  const nav = resolveNav(pathname);
  if (!nav) return null;
  const { destination, tab: activeTab } = nav;
  if (destination.tabs.length < 2 || destination.id === "settings") return null;
  if (destination.id === "companies" && pathname !== activeTab.href) return null;

  const tabs = destination.tabs;
  const shown = tabs.slice(0, VISIBLE);
  const more = tabs.slice(VISIBLE);
  const activeInMore = more.some((t) => t.href === activeTab.href);

  return (
    <div className="mx-auto max-w-[1280px] px-4 md:px-6 lg:px-8">
      {/* Desktop: text tabs */}
      <nav aria-label={`${destination.label} pages`} className="hidden border-b border-border-soft md:block">
        <ul className="flex items-stretch gap-6">
          {shown.map((t) => (
            <li key={t.href}>
              <TabLink tab={t} active={t.href === activeTab.href} />
            </li>
          ))}
          {more.length ? (
            <li>
              <DropdownMenu>
                <DropdownMenuTrigger
                  className={cn(
                    "-mb-px inline-flex h-10 items-center gap-1 border-b-2 text-[13px] font-medium",
                    activeInMore ? "border-accent text-text" : "border-transparent text-text-muted hover:text-text",
                  )}
                >
                  {activeInMore ? activeTab.label : "More"}
                  <ChevronDownIcon aria-hidden="true" className="size-3.5" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  {more.map((t) => (
                    <DropdownMenuItem key={t.href} asChild>
                      <Link href={t.href} aria-current={t.href === activeTab.href ? "page" : undefined}>
                        {t.label}
                      </Link>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          ) : null}
        </ul>
      </nav>
      {/* Mobile: chip row */}
      <nav aria-label={`${destination.label} pages`} className="-mx-4 overflow-x-auto px-4 [scrollbar-width:none] md:hidden">
        <ul className="flex gap-2 py-1">
          {tabs.map((t) => {
            const on = t.href === activeTab.href;
            return (
              <li key={t.href} className="shrink-0">
                <Link
                  href={t.href}
                  aria-current={on ? "page" : undefined}
                  ref={on ? (el) => el?.scrollIntoView({ inline: "center", block: "nearest" }) : undefined}
                  className={cn(
                    "inline-flex h-8 items-center rounded-full px-3 text-[13px] font-medium",
                    on ? "bg-accent text-accent-foreground" : "bg-surface-muted text-text-muted",
                  )}
                >
                  {t.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

function TabLink({ tab, active }: { tab: NavTab; active: boolean }) {
  return (
    <Link
      href={tab.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "-mb-px inline-flex h-10 items-center border-b-2 text-[13px] font-medium whitespace-nowrap transition-colors",
        active ? "border-accent text-text" : "border-transparent text-text-muted hover:text-text",
      )}
    >
      {tab.label}
    </Link>
  );
}
