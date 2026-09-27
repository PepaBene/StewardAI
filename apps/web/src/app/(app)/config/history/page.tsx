import type { Metadata } from "next";
import Link from "next/link";

import { CONFIG_DOMAINS, DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";

export const metadata: Metadata = { title: "Config history" };

export default function ConfigHistoryIndex() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-text-muted">
          <Link href="/config" className="hover:text-text hover:underline">
            Config
          </Link>{" "}
          / History
        </p>
        <h1 className="page-title">Version history</h1>
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Pick a config domain to see its saved versions, compare any two, and roll back.
        </p>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {DOMAIN_SLUGS.map((slug) => (
          <li key={slug}>
            <Link
              href={historyHref(slug)}
              className="block rounded-lg border border-border bg-surface-raised px-4 py-3 font-medium transition-colors hover:border-accent"
            >
              {CONFIG_DOMAINS[slug].label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
