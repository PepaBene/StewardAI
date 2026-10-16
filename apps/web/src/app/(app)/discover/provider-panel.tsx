"use client";

import type { CatalogueResponse } from "@/lib/discover/discover";
import { cn } from "@/lib/utils";

/** Plain wording for a provider's state; no internal status names reach the page. */
const STATE_TEXT: Record<string, string> = {
  available: "is connected",
  access_unverified: "is set up but has not been used yet",
  not_configured: "is not set up",
  denied: "refused the key, so check it in Settings",
  paid_only: "needs a paid plan",
  rate_limited: "is busy, so try again later",
};

export function stateText(state: string): string {
  return STATE_TEXT[state] ?? "is not available right now";
}

function providerName(provider: string): string {
  return provider.length <= 4 ? provider.toUpperCase() : provider.charAt(0).toUpperCase() + provider.slice(1);
}

/**
 * The basic-screen switch, shown inside the advanced filters. "Basic screen" limits the builder
 * to filters that need no provider key; the list says what each optional provider would add.
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
    <div className="space-y-2 rounded-lg bg-surface-muted px-4 py-3 text-sm">
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={basic} onChange={(e) => onBasic(e.target.checked)} className="mt-1" />
        <span>
          Basic filters only
          <span className="block max-w-[72ch] text-xs text-text-muted">
            Uses the <span className="num">{keyless}</span> filters that need no extra data provider: statements, prices, identity and fund holdings.
          </span>
        </span>
      </label>
      {providers.length > 0 ? (
        <ul className="space-y-1 text-xs" aria-label="Optional data providers">
          {providers.map((p) => (
            <li key={p.provider} className="flex flex-wrap gap-x-2">
              <span className={cn("font-medium", p.state === "available" ? "text-gain" : "text-text-muted")}>
                {providerName(p.provider)} {stateText(p.state)}
              </span>
              <span className="text-text-muted">
                {p.state === "available" ? "and adds" : "and would add"} {p.unlocks.join(", ")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {locked.length > 0 ? (
        <p className="max-w-[72ch] text-xs text-text-muted">
          Filters that rely on a provider you have not connected are greyed out with the reason. Nothing is guessed in their place.
        </p>
      ) : null}
    </div>
  );
}
