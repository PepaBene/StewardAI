import Link from "next/link";

import { cn } from "@/lib/utils";

const TABS = [
  { id: "daily", label: "Daily digests", href: "/reports" },
  { id: "weekly", label: "Weekly digests", href: "/reports?period=weekly" },
  { id: "escalations", label: "Impact notes", href: "/escalations" },
] as const;

export type ReportsTab = (typeof TABS)[number]["id"];

/** Tabs of the Reports section: the digests by period and the escalation notes. */
export function ReportsTabs({ active }: { active: ReportsTab }) {
  return (
    <nav aria-label="Report type" className="flex gap-1 overflow-x-auto border-b border-border">
      {TABS.map((tab) => (
        <Link
          key={tab.id}
          href={tab.href}
          aria-current={tab.id === active ? "page" : undefined}
          className={cn(
            "-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap",
            tab.id === active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
          )}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
