import Link from "next/link";

import { cn } from "@/lib/utils";

const TABS = [
  { id: "feed", label: "Feed", title: "Stories about your holdings", href: "/news" },
  { id: "wire", label: "Wire", title: "Every headline from every source", href: "/news/wire" },
  { id: "stories", label: "Stories", title: "Developing situations with their timeline", href: "/news/stories" },
  { id: "brief", label: "Brief", title: "Morning and weekly briefs", href: "/news/brief" },
  { id: "ask", label: "Ask", title: "Ask a question over the news", href: "/news/ask" },
  { id: "sources", label: "Sources", title: "Health of every feed", href: "/news/sources" },
  { id: "ops", label: "Review", title: "Cost, alert speed and stories to review", href: "/news/ops" },
] as const;

export type NewsTab = (typeof TABS)[number]["id"];

/** The news pages as one row of tabs: the holding feed, the wire, stories, briefs, Ask and source health. */
export function NewsTabs({ active, className }: { active: NewsTab; className?: string }) {
  return (
    <nav aria-label="News pages" className={cn("flex overflow-x-auto border-b border-border", className)}>
      {TABS.map((tab) => (
        <Link
          key={tab.id}
          href={tab.href}
          title={tab.title}
          aria-current={tab.id === active ? "page" : undefined}
          className={cn(
            "-mb-px border-b-2 px-4 py-3 text-center text-[13px] whitespace-nowrap",
            tab.id === active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
          )}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
