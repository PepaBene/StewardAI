"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { resolveNav } from "@/lib/nav";

/** Renders its children only on Portfolio pages (the market strip lives there, plan §2A.6). */
export function PortfolioOnly({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return resolveNav(pathname)?.destination.id === "portfolio" ? <>{children}</> : null;
}
