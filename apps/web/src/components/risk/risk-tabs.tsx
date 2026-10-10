"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/risk", label: "Risk" },
  { href: "/risk/ex-ante", label: "Today’s risk" },
  { href: "/risk/tail", label: "Bad months" },
  { href: "/risk/replay", label: "Past episodes" },
  { href: "/risk/factors", label: "Factors" },
  { href: "/risk/stress", label: "Stress test" },
  { href: "/risk/themes", label: "Themes" },
  { href: "/risk/whatif", label: "What if" },
  { href: "/risk/rebalance", label: "Rebalance" },
  { href: "/risk/pretrade", label: "Before a trade" },
  { href: "/risk/deposit", label: "Size and deposit" },
  { href: "/risk/validation", label: "Does it hold up" },
] as const;

export function RiskTabs() {
  const pathname = usePathname();
  return (
    <div className="border-b border-border">
      <nav aria-label="Risk and decisions" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {TABS.map((tab) => {
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
