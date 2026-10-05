"use client";

import { useCallback, useMemo, useState } from "react";

import { OwnerTag } from "@/components/reference/form-bits";
import { Field } from "@/components/reference/form-bits";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";

type Context = components["schemas"]["ResearchContext"];
type Claim = Context["claims"][number];

const PROVENANCE: Record<Claim["provenance"], string> = {
  primary: "Primary source",
  vendor: "Provider-reported",
  owner: "Entered by you",
  community: "Community-sourced",
  unclassified: "Source type unknown",
};

function claimValue(c: Claim): string {
  if (c.text_value) return c.text_value;
  const n = typeof c.value === "number" ? c.value : c.value === null || c.value === undefined || c.value === "" ? null : Number(c.value);
  if (n === null || !Number.isFinite(n)) return "Unknown";
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: 4 }).format(n)}${c.unit ? ` ${c.unit}` : ""}${c.currency ? ` ${c.currency}` : ""}`;
}

/** Claims grouped by the domain they come from, each with its source, period and who supplied it, then the gaps. */
function ContextCard({ ctx }: { ctx: Context }) {
  const byDomain = useMemo(() => {
    const m = new Map<string, Claim[]>();
    for (const c of ctx.claims) m.set(c.domain, [...(m.get(c.domain) ?? []), c]);
    return [...m.entries()];
  }, [ctx.claims]);
  return (
    <div className="space-y-3">
      <p className="text-xs text-text-muted">
        {titleCase(ctx.scope)}: {ctx.subject}, known as of {formatDate(ctx.as_of)}. {ctx.method ?? ""}
      </p>
      {byDomain.length === 0 ? <p className="text-sm text-text-muted">No claim is stored for this subject.</p> : null}
      {byDomain.map(([domain, claims]) => (
        <section key={domain} aria-label={titleCase(domain)}>
          <h4 className="mb-1 text-sm font-semibold">{titleCase(domain)}</h4>
          <ul className="divide-y divide-border border border-border">
            {claims.map((c) => (
              <li key={c.claim_id} className="grid gap-x-3 px-3 py-1.5 text-sm sm:grid-cols-[1fr_auto]">
                <span>
                  {c.label}
                  <span className="block text-xs text-text-muted">
                    {c.period}. {c.source}. {c.provenance === "owner" ? <OwnerTag /> : PROVENANCE[c.provenance]}
                    {c.note ? `. ${c.note}` : ""}
                  </span>
                </span>
                <span className="num sm:text-right">{claimValue(c)}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {ctx.gaps.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
          {ctx.gaps.map((g) => (
            <li key={`${g.domain}${g.reason}`}>
              {titleCase(g.domain)}: {g.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function useCard(call: (() => Promise<{ data?: Context; error?: unknown; response: Response }>) | null) {
  const fetcher = useCallback(async () => (call ? call() : { data: undefined, error: { detail: "Enter a value to look up." }, response: new Response(null, { status: 400 }) }), [call]);
  return useApiResource<Context>(fetcher);
}

/** GET /research-context/company/{ticker}, /country/{geography} and /industry/{hs_code}. */
export function ResearchContextPanel({ ticker }: { ticker: string }) {
  const [geo, setGeo] = useState("");
  const [hs, setHs] = useState("");
  const geoOk = /^[A-Za-z0-9]{2,5}$/.test(geo.trim());
  const hsOk = /^[0-9]{2,6}$/.test(hs.trim());
  const api = getBrowserApiClient();
  const company = useCard(useCallback(() => api.GET("/research-context/company/{ticker}", { params: { path: { ticker }, query: {} } }), [api, ticker]));
  const country = useCard(useCallback(() => (geoOk ? api.GET("/research-context/country/{geography}", { params: { path: { geography: geo.trim().toUpperCase() }, query: {} } }) : Promise.resolve({ data: undefined, error: null, response: new Response(null, { status: 400 }) })), [api, geo, geoOk]));
  const industry = useCard(useCallback(() => (hsOk ? api.GET("/research-context/industry/{hs_code}", { params: { path: { hs_code: hs.trim() }, query: {} } }) : Promise.resolve({ data: undefined, error: null, response: new Response(null, { status: 400 }) })), [api, hs, hsOk]));

  const body = (r: ReturnType<typeof useCard>) => {
    const d = r.state.kind === "ok" ? r.state.data : r.state.kind === "error" ? r.state.previous : null;
    if (d) return <ContextCard ctx={d} />;
    if (r.state.kind === "loading") return <p className="text-sm text-text-muted">Loading…</p>;
    return <p className="text-sm text-text-muted">Unavailable: {r.state.kind === "error" ? r.state.title : "unknown"}</p>;
  };

  return (
    <Blueprint aria-labelledby="rc-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Research context" title={<span id="rc-heading">Everything stored about the company, a country and an industry</span>} />
      <p className="max-w-[68ch] text-sm text-text-muted">
        Each figure keeps the source that reported it and whether that source is primary, a provider, community-sourced or you. Where nothing is stored, the gap is listed instead of a number.
      </p>
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-2">
          <h3 className="text-base font-semibold">Company: {ticker}</h3>
          {body(company)}
        </div>
        <div className="space-y-2">
          <Field label="Country code" hint="For example US, DE" error={geo.trim() !== "" && !geoOk ? "Use 2 to 5 letters or digits." : undefined}>
            <Input value={geo} onChange={(e) => setGeo(e.target.value)} maxLength={5} />
          </Field>
          {geoOk ? body(country) : <p className="text-sm text-text-muted">Enter a country code to see what is stored for it.</p>}
        </div>
        <div className="space-y-2">
          <Field label="HS product code" hint="2 to 6 digits" error={hs.trim() !== "" && !hsOk ? "Use 2 to 6 digits." : undefined}>
            <Input inputMode="numeric" value={hs} onChange={(e) => setHs(e.target.value)} maxLength={6} />
          </Field>
          {hsOk ? body(industry) : <p className="text-sm text-text-muted">Enter an HS code to see what is stored for that industry.</p>}
        </div>
      </div>
    </Blueprint>
  );
}
