"use client";

import { ChevronDownIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { CLUSTERS, isClusterActive, isPageActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

const TAB =
  "inline-flex h-10 items-center gap-1 border-b-2 px-3 text-sm transition-colors";
const TAB_ACTIVE = "border-accent font-semibold text-text";
const TAB_INACTIVE = "border-transparent text-text-muted hover:text-text";

/**
 * The five-cluster IA (Ledger_Design_System_Redesign_Proposal.md section 3): Portfolio,
 * Research & Verification, Oversight and Config each open a menu of their pages; a
 * single-page cluster (Config) is a plain link. Chat and the command palette are ambient,
 * reached from the header, never a tab here.
 */
export function AppNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Sections" className="-mb-px overflow-x-auto">
      <ul className="flex min-w-max gap-1">
        {CLUSTERS.map((cluster) => {
          const active = isClusterActive(cluster, pathname);
          if (cluster.pages.length === 1) {
            return (
              <li key={cluster.id}>
                <Link
                  href={cluster.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(TAB, active ? TAB_ACTIVE : TAB_INACTIVE)}
                >
                  {cluster.label}
                </Link>
              </li>
            );
          }
          return (
            <li key={cluster.id}>
              <DropdownMenu>
                <DropdownMenuTrigger
                  aria-current={active ? "page" : undefined}
                  className={cn(TAB, active ? TAB_ACTIVE : TAB_INACTIVE)}
                >
                  {cluster.label}
                  <ChevronDownIcon aria-hidden="true" className="size-3.5" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  {cluster.pages.map((page) => {
                    const pageActive = isPageActive(page.href, pathname);
                    return (
                      <DropdownMenuItem key={page.href} asChild>
                        <Link
                          href={page.href}
                          aria-current={pageActive ? "page" : undefined}
                          className={pageActive ? "font-semibold text-text" : undefined}
                        >
                          {page.label}
                        </Link>
                      </DropdownMenuItem>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
