import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { parseIndex } from "@/lib/methods/markdown";
import { methodSlugs, readMethod } from "@/lib/methods/read";

export const dynamic = "force-static";
export const metadata: Metadata = { title: "Methods" };

export default function MethodsPage() {
  const described = new Map(parseIndex(readMethod("index") ?? "").map((e) => [e.slug, e.description]));
  const slugs = methodSlugs();
  return (
    <div className="flex flex-col gap-5">
      <PageHeader kicker="Reference" title="Methods" description="How each insight figure is calculated, the thresholds it uses, when it cannot be given and the hand-worked tests that pin it." />
      <Blueprint className="px-[22px] py-5">
        {slugs.length === 0 ? (
          <p className="text-sm text-text-muted">No method documents were found when the app was built.</p>
        ) : (
          <ul className="divide-y divide-border-soft">
            {slugs.map((s) => (
              <li key={s} className="py-2.5">
                <Link href={`/methods/${s}`} className="font-medium text-accent-ink hover:underline">
                  {s.replaceAll("_", " ")}
                </Link>
                {described.get(s) ? <p className="mt-0.5 text-sm text-text-muted">{described.get(s)}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </Blueprint>
    </div>
  );
}
