"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/xray", label: "Exposure" },
  { href: "/xray/overlap", label: "Overlap" },
  { href: "/xray/concentration", label: "Concentration" },
  { href: "/xray/fundamentals", label: "Fundamentals" },
  { href: "/xray/benchmark", label: "Against a benchmark" },
  { href: "/xray/proposed", label: "What if" },
] as const;

export function XrayTabs() {
  const pathname = usePathname();
  return (
    <div className="border-b border-border">
      <nav aria-label="X-Ray views" className="-mb-px overflow-x-auto">
        <ul className="flex">
          {TABS.map((tab) => {
            const active = tab.href === "/xray" ? pathname === tab.href : pathname.startsWith(tab.href);
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
