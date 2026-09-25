import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/empty-state";
import { PLACEHOLDER_SECTIONS } from "@/lib/nav";

// Placeholder pages for sections built in later phases. Unknown slugs 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return PLACEHOLDER_SECTIONS.map((s) => ({ section: s.slug }));
}

function findSection(slug: string) {
  return PLACEHOLDER_SECTIONS.find((s) => s.slug === slug);
}

export async function generateMetadata({
  params,
}: PageProps<"/[section]">): Promise<Metadata> {
  const section = findSection((await params).section);
  return { title: section?.label };
}

export default async function SectionPage({ params }: PageProps<"/[section]">) {
  const section = findSection((await params).section);
  if (!section) notFound();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{section.label}</h1>
        <p className="mt-1 text-sm text-text-muted">{section.description}</p>
      </div>
      <EmptyState title="Not built yet">
        This section arrives in a later phase.
      </EmptyState>
    </div>
  );
}
