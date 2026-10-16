"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const VIEWS = [
  { href: "/engine-ledger", label: "What the engine did" },
  { href: "/autopilot", label: "Decisions and calibration" },
] as const;

/** System › Autonomy has two views: the engine's ledger and Autopilot's decisions. */
export function AutonomySubnav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Autonomy views" className="inline-flex w-fit gap-0.5 rounded-control bg-surface-muted p-0.5">
      {VIEWS.map((v) => {
        const on = pathname === v.href;
        return (
          <Link
            key={v.href}
            href={v.href}
            aria-current={on ? "page" : undefined}
            className={cn(
              "inline-flex h-8 items-center rounded-md px-3 text-[13px] font-medium",
              on ? "bg-surface-raised text-text" : "text-text-muted hover:text-text",
            )}
          >
            {v.label}
          </Link>
        );
      })}
    </nav>
  );
}
