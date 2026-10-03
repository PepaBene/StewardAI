import type { ReactNode } from "react";

import { isExternalUrl } from "@/lib/sources/sources";

/** A source link that is only a link for an http(s) URL; any other scheme renders as plain text. */
export function SafeLink({ href, className, children }: { href: string | null | undefined; className?: string; children?: ReactNode }) {
  if (!isExternalUrl(href)) return <span className={className?.replace(/text-accent-ink|hover:underline/g, "").trim()}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className={className}>
      {children}
    </a>
  );
}
