"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { openChatPanel } from "@/components/chat/chat-panel";
import { EntityLayout, type EntitySectionLink } from "@/components/layout/entity-layout";
import { ScoreMark } from "@/components/insight/score-mark";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { fundHref, lagDays, type FundProfile } from "@/lib/fundresearch/fund-research";
import { FUND_AXES, FUND_AXIS_NAMES, fundMarkMissing, fundMarkPetals, ongoingChargeFrom, type FundMarkInput } from "@/lib/funds/mark";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

const ProfileContext = createContext<ResourceState<FundProfile>>({ kind: "loading" });

/** The fund's profile, fetched once by the shell and shared by the sidebar and the Overview section. */
export function useFundProfile(): ResourceState<FundProfile> {
  return useContext(ProfileContext);
}

/** Sections in the order the plan names them; the slugs are the existing routes. */
const FUND_SECTIONS = [
  { slug: "", label: "Overview" },
  { slug: "holdings", label: "Holdings" },
  { slug: "performance", label: "Performance" },
  { slug: "style", label: "Style" },
  { slug: "bond", label: "Bond" },
  { slug: "checklist", label: "Checklist" },
] as const;

/** What the profile alone can tell the mark; the holdings and history axes stay gaps until stored. */
function markInput(p: FundProfile | null): FundMarkInput {
  return {
    ongoingChargePct: ongoingChargeFrom(p?.charges),
    holdingsCount: null,
    top10Weight: null,
    yearsOfHistory: null,
    holdingsLagDays: lagDays(p?.holdings_as_of),
    fieldsHave: p ? p.have : null,
    fieldsOf: p ? p.of : null,
  };
}

function FundMark({ p, name, size }: { p: FundProfile | null; name: string; size: "sidebar" | "header" }) {
  const input = markInput(p);
  return <ScoreMark petals={fundMarkPetals(input)} missing={fundMarkMissing(input)} axisNames={FUND_AXIS_NAMES} size={size} label={name} />;
}

function Identity({ p, isin, compact }: { p: FundProfile | null; isin: string; compact?: boolean }) {
  const name = p?.name ?? shortTicker(isin);
  const lag = lagDays(p?.holdings_as_of);
  return (
    <>
      <div className={cn("flex gap-3", compact ? "items-center" : "flex-col")}>
        <FundMark p={p} name={name} size={compact ? "header" : "sidebar"} />
        <div className="min-w-0">
          <h1 className="page-title mb-1">{name}</h1>
          <p className="flex flex-wrap gap-1.5 text-xs">
            <span className="rounded-control bg-surface-muted px-1.5 py-0.5 font-mono">{isin}</span>
            {p?.provider ? <span className="rounded-control bg-surface-muted px-1.5 py-0.5">{p.provider}</span> : null}
            {p?.identity.share_class ? <span className="rounded-control bg-surface-muted px-1.5 py-0.5">share class {p.identity.share_class}</span> : null}
          </p>
        </div>
      </div>
      {!compact ? (
        <ul className="space-y-0.5 text-xs text-text-muted" aria-label="What the mark shows">
          {FUND_AXES.map((a) => (
            <li key={a.key} title={a.question}>
              {a.label}: {a.question.replace(/\?$/, "").toLowerCase()}
            </li>
          ))}
        </ul>
      ) : null}
      {p ? (
        <p className="text-xs text-text-muted">
          Holdings as of {p.holdings_as_of ? formatDate(p.holdings_as_of) : "an unknown date"}
          {lag !== null ? <span className={lag > 45 ? "text-unconfirmed" : undefined}>{` (${lag} days old)`}</span> : null}
          {` · ${p.have} of ${p.of} profile fields filled`}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => openChatPanel(`What should I know about the fund ${name}?`)}>
          Ask about this fund
        </Button>
        <Button asChild size="sm" variant="ghost">
          <Link href="/funds/research">All funds</Link>
        </Button>
      </div>
    </>
  );
}

export function FundShell({ isin, children }: { isin: string; children: ReactNode }) {
  const pathname = usePathname();
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/profile", { params: { path: { isin } } }), [isin]);
  const { state } = useApiResource(fetcher);
  const p = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  const sections: EntitySectionLink[] = useMemo(
    () =>
      FUND_SECTIONS.map((s) => {
        const href = fundHref(isin, s.slug);
        return { key: s.slug || "overview", href, label: s.label, active: s.slug === "" ? pathname === href : pathname.startsWith(href) };
      }),
    [isin, pathname],
  );

  return (
    <ProfileContext.Provider value={state}>
      <EntityLayout
        navLabel="Fund sections"
        sections={sections}
        sidebar={<Identity p={p} isin={isin} />}
        header={
          <div className="blueprint flex flex-col gap-3 rounded-card px-4 py-3">
            <Identity p={p} isin={isin} compact />
          </div>
        }
      >
        {children}
      </EntityLayout>
    </ProfileContext.Provider>
  );
}
