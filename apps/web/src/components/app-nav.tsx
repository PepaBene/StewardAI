"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { DESTINATIONS, resolveNav } from "@/lib/nav";
import { cn } from "@/lib/utils";

/** Destinations shown as text links in the top bar; Inbox and Settings have their own icons. */
const TOP = DESTINATIONS.filter((d) => d.id !== "settings" && d.id !== "inbox");

/**
 * The top bar's destinations (visual spec C.0): text links, the active one in full text
 * colour with an accent underline. Hidden below lg, where the bottom bar takes over.
 */
export function AppNav() {
  const pathname = usePathname();
  const active = resolveNav(pathname)?.destination.id;

  return (
    <nav aria-label="Main" className="hidden h-full lg:block">
      <ul className="flex h-full items-stretch gap-6">
        {TOP.map((d) => {
          const on = d.id === active;
          return (
            <li key={d.id} className="flex">
              <Link
                href={d.href}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "inline-flex items-center border-b-2 pt-0.5 text-[13px] font-medium transition-colors",
                  on ? "border-accent text-text" : "border-transparent text-text-muted hover:text-text",
                )}
              >
                {d.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
