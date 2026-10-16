"use client";

import { usePathname } from "next/navigation";
import type * as React from "react";

import { PageHeader } from "@/components/page-header";

/**
 * The header of a tabbed section (Risk, Performance, X-Ray) with the h1 following the active tab,
 * so every tab has its own heading. `titles` maps a tab's path to its h1; the longest matching
 * path wins, and `fallback` covers any path that is not listed.
 */
export function TabPageHeader({
  kicker,
  fallback,
  titles,
  description,
}: {
  kicker: React.ReactNode;
  fallback: string;
  titles: Record<string, string>;
  description: React.ReactNode;
}) {
  const pathname = usePathname();
  const match = Object.keys(titles)
    .filter((p) => pathname === p || pathname.startsWith(`${p}/`))
    .sort((a, b) => b.length - a.length)[0];
  return <PageHeader kicker={kicker} title={match ? titles[match] : fallback} description={description} />;
}
