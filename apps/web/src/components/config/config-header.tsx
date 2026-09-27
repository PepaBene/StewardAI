import { HistoryIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { ConfigDomainSlug } from "@/lib/api/client";
import { historyHref } from "@/lib/config/domains";

/** Breadcrumb, title, a link to the domain's version history, and a short explanation. */
export function ConfigHeader({
  title,
  domain,
  children,
}: {
  title: string;
  domain: ConfigDomainSlug;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="text-sm text-text-muted">
          <Link href="/config" className="hover:text-text hover:underline">
            Config
          </Link>{" "}
          / {title}
        </p>
        <h1 className="page-title">{title}</h1>
        {children ? <div className="mt-1 max-w-prose space-y-1 text-sm text-text-muted">{children}</div> : null}
      </div>
      <Button asChild variant="outline" size="sm">
        <Link href={historyHref(domain)}>
          <HistoryIcon aria-hidden="true" />
          History
        </Link>
      </Button>
    </div>
  );
}
