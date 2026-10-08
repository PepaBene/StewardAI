"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/insights", label: "Changes since last time" },
  { href: "/insights/sectors", label: "Sector map" },
] as const;

export function InsightTabs() {
  const pathname = usePathname();
  return (
    <div className="border-b border-border">
      <nav aria-label="What changed" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {TABS.map((tab) => {
            const active = tab.href === "/insights" ? pathname === tab.href : pathname.startsWith(tab.href);
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
