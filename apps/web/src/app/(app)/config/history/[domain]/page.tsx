import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { VersionHistory } from "@/components/config/version-history";
import { CONFIG_DOMAINS, DOMAIN_SLUGS, isDomainSlug } from "@/lib/config/domains";

// One page per config domain; unknown slugs 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return DOMAIN_SLUGS.map((domain) => ({ domain }));
}

export async function generateMetadata({ params }: PageProps<"/config/history/[domain]">): Promise<Metadata> {
  const { domain } = await params;
  return { title: isDomainSlug(domain) ? `${CONFIG_DOMAINS[domain].label} history` : "History" };
}

export default async function ConfigHistoryPage({ params, searchParams }: PageProps<"/config/history/[domain]">) {
  const { domain } = await params;
  // ?version=<id> opens that version's diff (the editors' "View diff" links).
  const raw = Number((await searchParams).version);
  const initialVersion = Number.isSafeInteger(raw) && raw > 0 ? raw : null;
  if (!isDomainSlug(domain)) notFound();
  // Keyed so switching domains starts from a fresh selection.
  return <VersionHistory key={domain} domain={domain} initialVersion={initialVersion} />;
}
