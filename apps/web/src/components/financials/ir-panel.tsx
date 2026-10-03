import { ExternalLinkIcon } from "lucide-react";

import { StatePlaceholder } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { IrRelease, IrSource } from "@/lib/financials/types";
import { safeHref } from "@/lib/financials/logic";
import { formatDate } from "@/lib/format";

function Link({ href, children }: { href: string; children: React.ReactNode }) {
  const safe = safeHref(href);
  // A stored URL that is not http(s) (javascript:, data:) is shown as text, never as a link.
  if (!safe) return <span className="min-w-0 truncate" title="Not an http(s) address, so not linked">{children}</span>;
  return (
    <a href={safe} target="_blank" rel="noreferrer noopener" className="inline-flex max-w-full items-center gap-0.5 text-accent-ink hover:underline">
      <span className="min-w-0 truncate">{children}</span>
      <ExternalLinkIcon aria-hidden="true" className="size-3 shrink-0" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

/** Issuer investor-relations pages the app polls, and the releases it versioned from them. */
export function IrPanel({ releases, sources }: { releases: readonly IrRelease[]; sources: readonly IrSource[] }) {
  return (
    <Blueprint as="section" aria-labelledby="ir-heading" className="flex min-w-0 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Company-published guidance and consensus" title={<span id="ir-heading">Issuer IR pages and releases</span>} />
      {sources.length === 0 && releases.length === 0 ? (
        <StatePlaceholder state="not_covered" reason="No investor-relations page is registered and no release is stored for this holding. Company-published consensus appears here only when an IR page is registered." minHeight={80} />
      ) : null}
      {sources.length > 0 ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="sr-only">Registered IR pages and their last check</caption>
            <thead className="bg-surface-muted text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <tr>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Page</th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Type</th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Last check</th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id} className="border-b border-border-soft align-top">
                  <th scope="row" className="max-w-[16rem] px-3 py-1.5 text-left font-normal">
                    <Link href={s.url}>{s.label ?? s.url}</Link>
                    {s.active ? null : <span className="ml-1 text-xs text-text-muted">(paused)</span>}
                  </th>
                  <td className="px-3 py-1.5 text-text-muted">{s.releaseType.replaceAll("_", " ")}{s.basis ? ` · ${s.basis}` : ""}</td>
                  <td className="num px-3 py-1.5 text-text-muted">{s.lastCheckedAt ? formatDate(s.lastCheckedAt.slice(0, 10)) : "never"}</td>
                  <td className="px-3 py-1.5 text-text-muted">{s.lastError ?? s.lastStatus ?? "no outcome yet"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {releases.length > 0 ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="sr-only">Stored IR releases, newest first</caption>
            <thead className="bg-surface-muted text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <tr>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Release</th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Type</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Published</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Known since</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Analysts</th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {releases.map((r) => (
                <tr key={r.id} className="border-b border-border-soft align-top">
                  <th scope="row" className="max-w-[16rem] px-3 py-1.5 text-left font-normal">
                    <Link href={r.sourceUrl}>{r.sourceUrl}</Link>
                  </th>
                  <td className="px-3 py-1.5 text-text-muted">{r.releaseType.replaceAll("_", " ")}{r.basis ? ` · ${r.basis}` : ""}</td>
                  <td className="num px-3 py-1.5 text-right">{r.publishedAt ? formatDate(r.publishedAt.slice(0, 10)) : "not stated"}</td>
                  <td className="num px-3 py-1.5 text-right text-text-muted">{r.knownAt ? formatDate(r.knownAt.slice(0, 10)) : "—"}</td>
                  <td className="num px-3 py-1.5 text-right">{r.analystCount === null ? "not stated" : r.analystCount}</td>
                  <td className="px-3 py-1.5 text-text-muted">{r.status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Blueprint>
  );
}
