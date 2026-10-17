import Link from "next/link";

import { cn } from "@/lib/utils";

const TABS = [
  { id: "daily", label: "Daily", title: "Daily digests", href: "/reports" },
  { id: "weekly", label: "Weekly", title: "Weekly digests", href: "/reports?period=weekly" },
  { id: "monthly", label: "Monthly", title: "Monthly digests: the month's news, moves and strategy", href: "/reports?period=monthly" },
  { id: "quarterly", label: "Quarterly", title: "Quarterly digests: the quarter's news, moves and strategy", href: "/reports?period=quarterly" },
  { id: "reviews", label: "Compliance", title: "Monthly compliance reviews: every rule checked, with proposed actions", href: "/reviews" },
  { id: "escalations", label: "Impact", title: "Impact notes (escalations)", href: "/escalations" },
  { id: "research", label: "Research", title: "Research reports", href: "/research" },
] as const;

export type ReportsTab = (typeof TABS)[number]["id"];

/**
 * Tabs of the Reports list: the digests by period, monthly reviews, impact notes and
 * research. The design's underlined segment row, sized to sit on top of the list panel.
 */
export function ReportsTabs({ active, className }: { active: ReportsTab; className?: string }) {
  return (
    <nav aria-label="Report type" className={cn("flex overflow-x-auto overflow-y-hidden border-b border-border [scrollbar-width:none]", className)}>
      {TABS.map((tab) => (
        <Link
          key={tab.id}
          href={tab.href}
          title={tab.title}
          aria-current={tab.id === active ? "page" : undefined}
          className={cn(
            "flex-1 border-b-2 px-2 py-3 text-center text-[13px] whitespace-nowrap",
            tab.id === active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
          )}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
