"use client";

import { BriefcaseIcon, EllipsisIcon, FlaskConicalIcon, NewspaperIcon, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { InboxBell } from "@/components/inbox-bell";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DESTINATIONS, type DestinationId, resolveNav } from "@/lib/nav";
import { cn } from "@/lib/utils";

const MAIN: { id: DestinationId; icon: LucideIcon }[] = [
  { id: "portfolio", icon: BriefcaseIcon },
  { id: "research", icon: FlaskConicalIcon },
  { id: "markets", icon: NewspaperIcon },
];
const MORE: DestinationId[] = ["companies", "briefings", "system", "settings"];

/**
 * Mobile navigation (below lg): Portfolio · Research · Markets · Inbox · More, fixed to the
 * bottom edge and clear of the home indicator. "More" opens the other destinations.
 */
export function BottomBar() {
  const pathname = usePathname();
  const active = resolveNav(pathname)?.destination.id;
  const byId = new Map(DESTINATIONS.map((d) => [d.id, d]));
  const moreActive = active ? MORE.includes(active) : false;

  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border-soft bg-page pb-[env(safe-area-inset-bottom,0px)] lg:hidden"
    >
      <ul className="mx-auto grid h-16 max-w-lg grid-cols-5 items-center">
        {MAIN.map(({ id, icon: Icon }) => {
          const d = byId.get(id);
          if (!d) return null;
          const on = active === id;
          return (
            <li key={id} className="flex justify-center">
              <Link
                href={d.href}
                aria-current={on ? "page" : undefined}
                className={cn("flex flex-col items-center gap-0.5 text-xs", on ? "text-accent" : "text-text-muted")}
              >
                <Icon aria-hidden="true" className="size-6" />
                {d.label}
              </Link>
            </li>
          );
        })}
        <li className="flex justify-center">
          <InboxBell variant="tab" />
        </li>
        <li className="flex justify-center">
          <DropdownMenu>
            <DropdownMenuTrigger
              className={cn("flex flex-col items-center gap-0.5 text-xs", moreActive ? "text-accent" : "text-text-muted")}
            >
              <EllipsisIcon aria-hidden="true" className="size-6" />
              More
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="end" className="min-w-52">
              {MORE.map((id) => {
                const d = byId.get(id);
                if (!d) return null;
                return (
                  <DropdownMenuItem key={id} asChild>
                    <Link href={d.href} aria-current={active === id ? "page" : undefined} className="py-2.5">
                      {d.label}
                    </Link>
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        </li>
      </ul>
    </nav>
  );
}
