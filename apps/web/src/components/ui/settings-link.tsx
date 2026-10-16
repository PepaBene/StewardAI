import { SettingsIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * F40 SettingsLink: the one-line pointer left where a form moved out of a report page into
 * Settings (plan P20): what is set there, and a link to change it.
 */
export function SettingsLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <p className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-text-muted", className)}>
      <SettingsIcon aria-hidden="true" className="size-4 shrink-0 text-text-faint" />
      <span>{children}</span>
      <Link href={href} className="font-medium text-accent-ink hover:underline">
        Change in Settings
      </Link>
    </p>
  );
}
