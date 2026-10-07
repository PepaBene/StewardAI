"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/performance", label: "Returns" },
  { href: "/performance/attribution", label: "Contribution" },
  { href: "/performance/lots", label: "Lots and holding periods" },
  { href: "/performance/core-vs-stocks", label: "Core and stocks" },
  { href: "/performance/ledger", label: "Ledger and settings" },
] as const;

export function PerformanceTabs() {
  const pathname = usePathname();
  return (
    <div className="border-b border-border">
      <nav aria-label="Performance views" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {TABS.map((tab) => {
            const active = tab.href === "/performance" ? pathname === tab.href : pathname.startsWith(tab.href);
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
