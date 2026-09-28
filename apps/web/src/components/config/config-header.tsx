import { HistoryIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import type { ConfigDomainSlug } from "@/lib/api/client";
import { historyHref } from "@/lib/config/domains";

/**
 * The page header every config editor opens with: kicker "Config", the domain as title, a
 * short explanation, and on the right a link to the domain's version history (after
 * anything passed as `aside`, e.g. the active version).
 */
export function ConfigHeader({
  title,
  domain,
  aside,
  children,
}: {
  title: string;
  domain: ConfigDomainSlug;
  aside?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <PageHeader
      kicker={
        <Link href="/config" className="hover:underline">
          Config
        </Link>
      }
      title={title}
      description={children ? <div className="max-w-prose space-y-1">{children}</div> : undefined}
      aside={
        <>
          {aside}
          <Button asChild variant="outline" size="sm">
            <Link href={historyHref(domain)}>
              <HistoryIcon aria-hidden="true" />
              History
            </Link>
          </Button>
        </>
      }
    />
  );
}
