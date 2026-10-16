"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { SummaryPanel } from "@/components/config/summary";
import { getBrowserApiClient, type ConfigDomainSlug, type ConfigVersionMeta } from "@/lib/api/client";
import { CONFIG_DOMAINS, DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { formatDateTime } from "@/lib/format";

type Versions = Partial<Record<ConfigDomainSlug, ConfigVersionMeta[] | "error">>;

const SHOWN = 3;

const changeWords = (n: number | null) =>
  n === null ? "First version" : n === 0 ? "No changes" : `${n} change${n === 1 ? "" : "s"}`;

/** A short version timeline per config domain: when it changed, who saved it and why. */
export function HistoryTimelines() {
  const [versions, setVersions] = useState<Versions>({});

  useEffect(() => {
    let cancelled = false;
    const api = getBrowserApiClient();
    for (const domain of DOMAIN_SLUGS) {
      api
        .GET("/config/{domain}/versions", { params: { path: { domain } } })
        .then((r) => {
          if (!cancelled) setVersions((v) => ({ ...v, [domain]: r.data ?? "error" }));
        })
        .catch(() => {
          if (!cancelled) setVersions((v) => ({ ...v, [domain]: "error" }));
        });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const loaded = DOMAIN_SLUGS.filter((s) => versions[s] !== undefined).length;
  const total = DOMAIN_SLUGS.reduce((n, s) => {
    const v = versions[s];
    return n + (Array.isArray(v) ? v.length : 0);
  }, 0);

  return (
    <SummaryPanel
      title="Recent changes by area"
      reading={
        loaded < DOMAIN_SLUGS.length
          ? "Loading the saved versions…"
          : `${total} saved version${total === 1 ? "" : "s"} across ${DOMAIN_SLUGS.length} areas. Each area shows its latest ${SHOWN}.`
      }
    >
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {DOMAIN_SLUGS.map((slug) => {
          const v = versions[slug];
          const list = Array.isArray(v) ? v : [];
          return (
            <li key={slug} className="min-w-0 space-y-2 rounded-card bg-surface px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-medium">{CONFIG_DOMAINS[slug].label}</h3>
                <Link
                  href={historyHref(slug)}
                  className="inline-flex items-center gap-0.5 rounded-control text-sm text-accent-ink outline-none underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-focus/30"
                  aria-label={`Open the ${CONFIG_DOMAINS[slug].label} history`}
                >
                  All {list.length > 0 ? list.length : ""} versions
                  <ChevronRightIcon aria-hidden="true" className="size-4" />
                </Link>
              </div>
              {v === undefined ? (
                <p className="text-sm text-text-muted">Loading…</p>
              ) : v === "error" ? (
                <p className="text-sm text-text-muted">The versions could not be loaded.</p>
              ) : list.length === 0 ? (
                <p className="text-sm text-text-muted">Nothing saved yet; the defaults are in use.</p>
              ) : (
                <ol className="space-y-2 border-l-2 border-chart-track pl-3">
                  {list.slice(0, SHOWN).map((x) => (
                    <li key={x.id} className="text-sm">
                      <p>
                        <span className="num">{formatDateTime(x.created_at)}</span>
                        {x.active ? <span className="ml-2 font-medium text-gain">Active</span> : null}
                      </p>
                      <p className="text-text-muted">
                        {changeWords(x.changes)}, saved by {x.created_by ?? "an unknown author"}
                        {x.note ? `: ${x.note}` : ""}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          );
        })}
      </ul>
    </SummaryPanel>
  );
}
