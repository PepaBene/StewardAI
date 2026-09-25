"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/holdings", label: "Positions" },
  { href: "/transactions", label: "Transactions and dividends" },
] as const;

/** Switches between the open positions and the transaction history, both part of Holdings. */
export function HoldingsTabs() {
  const pathname = usePathname();
  return (
    <nav aria-label="Holdings views">
      <ul className="flex gap-1 border-b border-border">
        {TABS.map((tab) => {
          const active = pathname === tab.href;
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px inline-flex h-9 items-center border-b-2 px-3 text-sm transition-colors",
                  active ? "border-accent font-medium text-text" : "border-transparent text-text-muted hover:text-text",
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
