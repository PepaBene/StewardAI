"use client";

import { InboxIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** How many items wait in the Inbox: unread agent items plus Autopilot's "needs you". */
async function loadCount(): Promise<number | null> {
  const api = getBrowserApiClient();
  const [inbox, needs] = await Promise.all([
    api.GET("/inbox", { params: { query: { status: "new" } } }).catch(() => null),
    api.GET("/autopilot/needs-you").catch(() => null),
  ]);
  const a = inbox?.data?.unread;
  const b = needs?.data?.count;
  if (a == null && b == null) return null;
  return (a ?? 0) + (b ?? 0);
}

/** The Inbox entry in the top bar and bottom bar, with its waiting count. */
export function InboxBell({ variant = "icon" }: { variant?: "icon" | "tab" }) {
  const pathname = usePathname();
  const [count, setCount] = useState<number | null>(null);
  const on = pathname === "/inbox" || pathname.startsWith("/inbox/");

  useEffect(() => {
    let cancelled = false;
    loadCount().then((n) => {
      if (!cancelled) setCount(n);
    });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const label = count ? `Inbox, ${count} waiting` : "Inbox";
  const badge = count ? (
    <span className="absolute -top-1 -right-1.5 min-w-[18px] rounded-full bg-accent px-1 text-center text-xs leading-[18px] font-semibold text-accent-foreground num">
      {count > 99 ? "99+" : count}
    </span>
  ) : null;

  if (variant === "tab") {
    return (
      <Link
        href="/inbox"
        aria-label={label}
        aria-current={on ? "page" : undefined}
        className={cn("flex flex-col items-center gap-0.5 text-xs", on ? "text-accent" : "text-text-muted")}
      >
        <span className="relative">
          <InboxIcon aria-hidden="true" className="size-6" />
          {badge}
        </span>
        Inbox
      </Link>
    );
  }

  return (
    <Link
      href="/inbox"
      aria-label={label}
      title={label}
      aria-current={on ? "page" : undefined}
      className={cn(
        "relative grid size-9 place-items-center rounded-control transition-colors hover:bg-surface-muted",
        on ? "text-text" : "text-text-muted",
      )}
    >
      <InboxIcon aria-hidden="true" className="size-[18px]" />
      {badge}
    </Link>
  );
}
