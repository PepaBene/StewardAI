"use client";

import type { CatalogueResponse } from "@/lib/discover/discover";
import { cn } from "@/lib/utils";

const STATE_TEXT: Record<string, string> = {
  available: "connected",
  access_unverified: "not checked yet",
  not_configured: "no key set",
  denied: "key rejected",
  paid_only: "needs a paid plan",
  rate_limited: "rate limited",
};

function stateText(state: string): string {
  return STATE_TEXT[state] ?? state.replace(/_/g, " ");
}

/**
 * What works without any provider key and what a missing provider switches off, so the absence
 * of an optional key reads as a known gap rather than a broken screen. "Basic screen" limits the
 * builder to the filters that need no key at all.
 */
export function ProviderPanel({
  catalogue,
  basic,
  onBasic,
}: {
  catalogue: CatalogueResponse;
  basic: boolean;
  onBasic: (next: boolean) => void;
}) {
  const providers = catalogue.providers ?? [];
  const keyless = catalogue.keyless_count ?? 0;
  const locked = providers.filter((p) => p.state !== "available");
  return (
    <div className="space-y-2 border border-border-soft bg-surface-sunken px-4 py-3 text-sm">
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={basic} onChange={(e) => onBasic(e.target.checked)} className="mt-1" />
        <span>
          Basic screen
          <span className="block max-w-[72ch] text-xs text-text-muted">
            Only filters that need no provider key: <span className="num">{keyless}</span> available now from statements, prices, identity and fund
            holdings.
          </span>
        </span>
      </label>
      {providers.length > 0 ? (
        <ul className="space-y-1 text-xs" aria-label="Optional providers">
          {providers.map((p) => (
            <li key={p.provider} className="flex flex-wrap gap-x-2">
              <span className={cn("font-medium", p.state === "available" ? "text-gain" : "text-unconfirmed")}>
                {p.provider.toUpperCase()} {stateText(p.state)}
              </span>
              <span className="text-text-muted">
                {p.state === "available" ? "adds" : "would add"} {p.unlocks.join(", ")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {locked.length > 0 ? (
        <p className="max-w-[72ch] text-xs text-text-muted">
          Filters that depend on a provider that is not connected show as unavailable with the reason. Nothing is estimated in their place and no value is
          read as zero.
        </p>
      ) : null}
    </div>
  );
}
