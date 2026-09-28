"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * Holdings and Transactions are the Portfolio cluster's two ledgers; the thesis board and
 * review queue live in the Research & Verification cluster of the main navigation.
 */
const TABS = [
  { href: "/holdings", label: "Holdings" },
  { href: "/transactions", label: "Transactions" },
] as const;

/**
 * Tab row between Holdings and Transactions, with an optional right-hand slot (the Holdings
 * page puts its Table / Heatmap / Charts switch there).
 */
export function HoldingsTabs({ aside }: { aside?: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-border">
      <nav aria-label="Holdings views" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {TABS.map((tab) => {
            const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
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
      {aside ? <div className="mb-2">{aside}</div> : null}
    </div>
  );
}
