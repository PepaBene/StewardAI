import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { MethodDocument } from "@/components/methods/method-document";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { parseMarkdown } from "@/lib/methods/markdown";
import { methodSlugs, readMethod } from "@/lib/methods/read";

// One page per document in docs/methods/, built ahead of time; unknown slugs 404.
export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  const slugs = methodSlugs();
  // The documents sit outside apps/web; a deploy that leaves them out would 404 every method
  // link, so the build fails instead.
  if (slugs.length === 0) throw new Error("docs/methods/ was not found next to apps/web: include files outside the root directory in the build.");
  return slugs.map((slug) => ({ slug }));
}

function titleOf(source: string, slug: string): string {
  return /^#\s+(.+)$/m.exec(source)?.[1]?.trim() ?? slug.replaceAll("_", " ");
}

export async function generateMetadata({ params }: PageProps<"/methods/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const source = readMethod(slug);
  return { title: source ? `${titleOf(source, slug)}: method` : "Method" };
}

export default async function MethodPage({ params }: PageProps<"/methods/[slug]">) {
  const { slug } = await params;
  const source = readMethod(slug);
  if (!source) notFound();
  const blocks = parseMarkdown(source);
  const summary = blocks.find((b) => b.kind === "p");
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Method"
        title={titleOf(source, slug)}
        description={summary && summary.kind === "p" ? summary.inline.map((p) => p.text).join("") : undefined}
      />
      <Blueprint className="px-[22px] py-5">
        <MethodDocument blocks={blocks.filter((b) => b !== summary)} />
      </Blueprint>
      <p className="text-xs text-text-muted">
        From <code className="font-mono">docs/methods/{slug}.md</code>.{" "}
        <Link href="/methods" className="text-accent-ink underline underline-offset-2">
          All methods
        </Link>
      </p>
    </div>
  );
}
