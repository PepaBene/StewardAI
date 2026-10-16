"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** Risk proper: how the portfolio behaves. */
const RISK_TABS = [
  { href: "/risk", label: "Risk" },
  { href: "/risk/ex-ante", label: "Today’s risk" },
  { href: "/risk/tail", label: "Bad months" },
  { href: "/risk/replay", label: "Past episodes" },
  { href: "/risk/factors", label: "Factors" },
  { href: "/risk/stress", label: "Stress test" },
  { href: "/risk/themes", label: "Themes" },
  { href: "/risk/validation", label: "Does it hold up" },
] as const;

/** Decide (plan §5.3): one question each, shown as modes of one tool rather than more tabs. */
export const DECIDE_MODES = [
  { href: "/risk/whatif", label: "What if", question: "What would a different mix of holdings have done?" },
  { href: "/risk/rebalance", label: "Rebalance", question: "Which trades bring the weights back to target?" },
  { href: "/risk/pretrade", label: "Before a trade", question: "What does one trade change before you place it?" },
  { href: "/risk/deposit", label: "Deposit", question: "Where should new money go, and how much fits?" },
] as const;

export function isDecidePath(pathname: string): boolean {
  return DECIDE_MODES.some((m) => pathname === m.href || pathname.startsWith(`${m.href}/`));
}

function DecideModes({ pathname }: { pathname: string }) {
  const active = DECIDE_MODES.find((m) => pathname === m.href || pathname.startsWith(`${m.href}/`));
  return (
    <div className="space-y-2">
      <nav aria-label="Decide">
        <ul className="inline-flex max-w-full flex-wrap gap-0.5 rounded-control bg-surface-muted p-0.5">
          {DECIDE_MODES.map((m) => (
            <li key={m.href}>
              <Link
                href={m.href}
                aria-current={m === active ? "page" : undefined}
                className={cn(
                  "inline-flex h-8 items-center rounded-control px-3 text-sm whitespace-nowrap",
                  m === active ? "bg-surface-raised font-semibold text-text shadow-sm" : "text-text-muted hover:text-text",
                )}
              >
                {m.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {active ? <p className="text-sm text-text-muted">{active.question} Nothing here sends an order.</p> : null}
    </div>
  );
}

export function RiskTabs() {
  const pathname = usePathname();
  if (isDecidePath(pathname)) return <DecideModes pathname={pathname} />;
  return (
    <div className="border-b border-border">
      <nav aria-label="Risk" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {RISK_TABS.map((tab) => {
            const active = tab.href === "/risk" ? pathname === tab.href : pathname.startsWith(tab.href);
            return (
              <li key={tab.href}>
                <Link
                  href={tab.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex h-10 items-center border-b-2 px-3.5 text-sm whitespace-nowrap transition-colors",
                    active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
                  )}
                >
                  {tab.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
