import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { CONFIG_DOMAINS, DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";

import { ConfigTransfer } from "../config-transfer";

export const metadata: Metadata = { title: "Config history" };

export default function ConfigHistoryIndex() {
  return (
    <div className="space-y-6">
      <PageHeader
        kicker={
          <Link href="/config" className="hover:underline">
            Config
          </Link>
        }
        title="Version history"
        description="Pick a config domain to see its saved versions, compare any two, and roll back. Export or import every domain at once below."
      />
      <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {DOMAIN_SLUGS.map((slug) => (
          <Blueprint as="li" key={slug} className="transition-colors hover:border-accent">
            <Link href={historyHref(slug)} className="block px-4 py-3 font-medium">
              {CONFIG_DOMAINS[slug].label}
            </Link>
          </Blueprint>
        ))}
      </ul>
      <ConfigTransfer />
    </div>
  );
}
