"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isSectionActive, SECTIONS, sectionHref } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Sections" className="-mb-px overflow-x-auto">
      <ul className="flex min-w-max gap-1">
        {SECTIONS.map((section) => {
          const href = sectionHref(section);
          const active = isSectionActive(href, pathname);
          return (
            <li key={section.label}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-10 items-center border-b-2 px-3 text-sm transition-colors",
                  active
                    ? "border-accent font-semibold text-text"
                    : "border-transparent text-text-muted hover:text-text",
                )}
              >
                {section.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
