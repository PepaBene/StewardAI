"use client";

import { normaliseSeparators } from "@/lib/number-input";
import Link from "next/link";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { type ApiResult, useApiResource } from "@/lib/hooks/use-api-resource";
import { Field, OwnerTag, SaveNote, type SaveState } from "@/components/reference/form-bits";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EntityName } from "@/components/entity-name";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDate, formatDateTime } from "@/lib/format";
import { apiErrorText } from "@/lib/reference/form-utils";
import { cellText, listIn, parseReturns } from "@/lib/reference/endpoint-forms";
import { arr, isRecord, recs, str, titleCase, type Rec } from "@/lib/parse-untyped";

/** A read-only panel over one GET: loading, error, and the body once it arrives. */
function ReadPanel<T>({ id, kicker, title, intro, fetcher, children }: { id: string; kicker: string; title: string; intro?: string; fetcher: () => Promise<ApiResult<T>>; children: (data: T) => ReactNode }) {
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  return (
    <Blueprint aria-labelledby={`${id}-heading`} className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker={kicker} title={<span id={`${id}-heading`}>{title}</span>} />
      {intro ? <p className="max-w-[68ch] text-sm text-text-muted">{intro}</p> : null}
      {state.kind === "loading" && !data ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !data ? (
        <p className="text-sm text-text-muted">
          Unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}
      {data !== null ? children(data) : null}
    </Blueprint>
  );
}

/** Records as a compact table: the first few scalar fields become columns. Missing values read "Unknown". */
function RecordTable({ rows, caption, max = 6 }: { rows: Rec[]; caption: string; max?: number }) {
  const cols = useMemo(() => {
    const keys: string[] = [];
    for (const r of rows) for (const [k, v] of Object.entries(r)) if (!keys.includes(k) && (v === null || typeof v !== "object")) keys.push(k);
    return keys.filter((k) => !/^(raw|detail|evidence|payload)$/.test(k)).slice(0, max);
  }, [rows, max]);
  if (rows.length === 0 || cols.length === 0) return null;
  return (
    <div className="relative overflow-x-auto border border-border">
      <table className="w-full min-w-[36rem] text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-xs text-text-muted">
            {cols.map((c) => (
              <th key={c} scope="col" className="px-3 py-2 text-left font-medium">
                {titleCase(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border align-top last:border-0">
              {cols.map((c) => (
                <td key={c} className={`px-3 py-2 ${typeof r[c] === "number" ? "num" : ""}`}>
                  {/(_at|_date|^date)$/.test(c) && typeof r[c] === "string" ? formatDate(r[c] as string) : cellText(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const Limits = ({ items }: { items: string[] }) =>
  items.length > 0 ? (
    <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
      {items.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  ) : null;

const Empty = ({ children }: { children: ReactNode }) => <p className="max-w-[68ch] text-sm text-text-muted">{children}</p>;

// ---- Returns ---------------------------------------------------------------------------------
const isoDay = (d: Date) => d.toISOString().slice(0, 10);

/** GET /holdings/{ticker}/returns: raw price, adjusted price and total return over a window you choose. */
export function ReturnsPanel({ ticker }: { ticker: string }) {
  const today = useMemo(() => new Date(), []);
  const [start, setStart] = useState(() => isoDay(new Date(today.getFullYear() - 1, today.getMonth(), today.getDate())));
  const [end, setEnd] = useState(() => isoDay(today));
  const [wh, setWh] = useState("");
  const [applied, setApplied] = useState({ start, end, wh: "" });
  const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(start) && /^\d{4}-\d{2}-\d{2}$/.test(end) && end >= start;
  const whNum = wh.trim() === "" ? 0 : Number(normaliseSeparators(wh)) / 100;
  const whOk = Number.isFinite(whNum) && whNum >= 0 && whNum < 1;
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/returns", {
        params: { path: { ticker }, query: { start: applied.start, end: applied.end, withholding_rate: applied.wh.trim() === "" ? 0 : Number(normaliseSeparators(applied.wh)) / 100 } },
      }),
    [ticker, applied],
  );
  return (
    <ReadPanel id="returns" kicker="Performance · computed on request" title="Returns over a window" fetcher={fetcher} intro="Raw price change, price change adjusted for splits, and total return including dividends. Total return is shown only when corporate actions are confirmed and complete; otherwise it stays unknown with the reason.">
      {(raw) => {
        const v = parseReturns(raw);
        const max = Math.max(0.0001, ...v.bars.map((b) => Math.abs(b.value ?? 0)));
        return (
          <>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (dateOk && whOk) setApplied({ start, end, wh });
              }}
              noValidate
            >
              <Field label="From" error={dateOk ? undefined : "Use YYYY-MM-DD, end not before start"} className="w-40">
                <Input value={start} onChange={(e) => setStart(e.target.value)} />
              </Field>
              <Field label="To" className="w-40">
                <Input value={end} onChange={(e) => setEnd(e.target.value)} />
              </Field>
              <Field label="Dividend tax withheld (%)" error={whOk ? undefined : "0 up to under 100"} hint="Optional" className="w-48">
                <Input inputMode="decimal" value={wh} onChange={(e) => setWh(e.target.value)} />
              </Field>
              <Button type="submit" size="sm" variant="outline" disabled={!dateOk || !whOk}>
                Compute
              </Button>
            </form>
            {v?.reason ? <Empty>{v.reason}</Empty> : null}
            {v && v.bars.length > 0 ? (
              <>
                <ul className="space-y-2" aria-label={`Returns for ${v.symbol ?? ticker}${v.currency ? ` in ${v.currency}` : ""}`}>
                  {v.bars.map((b) => (
                    <li key={b.id} className="grid grid-cols-[8rem_1fr_6rem] items-center gap-3 text-sm">
                      <span>{b.label}</span>
                      {b.value === null ? (
                        <span className="text-xs text-text-muted">{b.reason ?? "Unknown"}</span>
                      ) : (
                        <span role="img" aria-label={`${b.label} ${formatPct(b.value)}`} className="relative block h-3 border-x border-border-soft">
                          <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
                          <span
                            className={`absolute inset-y-0 ${b.value >= 0 ? "left-1/2 bg-accent" : "right-1/2 bg-text-muted"}`}
                            style={{ width: `${Math.min(50, (Math.abs(b.value) / max) * 50)}%` }}
                          />
                        </span>
                      )}
                      <span className="num text-right">{b.value === null ? "Unknown" : formatPct(b.value)}</span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-text-muted">
                  {v.start && v.end ? `${formatDate(v.start)} to ${formatDate(v.end)}. ` : ""}
                  {v.observed !== null && v.expected !== null ? `${v.observed} of ${v.expected} expected sessions have prices. ` : ""}
                  {v.actionCoverage ? `Corporate-action coverage: ${v.actionCoverage}. ` : ""}
                  {v.missingActions ? `${v.missingActions} action${v.missingActions === 1 ? "" : "s"} missing.` : ""}
                </p>
              </>
            ) : null}
          </>
        );
      }}
    </ReadPanel>
  );
}

// ---- Deals, KPI links, observations, PDMR, evidence ------------------------------------------
export function DealsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/deals", { params: { path: { ticker }, query: {} } }), [ticker]);
  return (
    <ReadPanel id="deals" kicker="Corporate events" title="Deals" fetcher={fetcher} intro="Each deal with the evidence behind its steps. Only confirmed steps set the terms and the dilution or leverage they imply.">
      {(d) => {
        const deals = listIn(d, ["deals"]);
        return (
          <>
            {deals.length === 0 ? <Empty>No deals are recorded for <EntityName id={ticker} chip={false} />.</Empty> : <RecordTable rows={deals} caption="Deals" />}
            <Limits items={arr(isRecord(d) ? d.limitations : null).filter((x): x is string => typeof x === "string")} />
          </>
        );
      }}
    </ReadPanel>
  );
}

export function ThesisKpiLinksPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/thesis-kpi-links", { params: { path: { ticker }, query: {} } }), [ticker]);
  return (
    <ReadPanel id="kpi-links" kicker="Thesis" title="Events linked to your thesis KPIs" fetcher={fetcher}>
      {(d) => {
        const links = listIn(d, ["links"]);
        return links.length === 0 ? <Empty>No event is linked to a thesis KPI for <EntityName id={ticker} chip={false} />.</Empty> : <RecordTable rows={links} caption="Events linked to thesis KPIs" />;
      }}
    </ReadPanel>
  );
}

export function CompanyObservationsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/company-research-observations", { params: { path: { ticker }, query: {} } }), [ticker]);
  return (
    <ReadPanel id="observations" kicker="Company research · vendor-reported" title="Company research observations" fetcher={fetcher} intro="Facts a research provider reported about the company. They are the provider's claims, not filings, and are kept apart from your own entries.">
      {(d) => {
        return d.observations.length === 0 ? <Empty>No provider has reported observations for <EntityName id={ticker} chip={false} />. Fetch them under Fetch more data.</Empty> : <RecordTable rows={d.observations} caption="Company research observations" />;
      }}
    </ReadPanel>
  );
}

export function PdmrPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/pdmr-notifications", { params: { path: { ticker }, query: {} } }), [ticker]);
  return (
    <ReadPanel id="pdmr" kicker="Insiders · manager notifications" title="PDMR notifications" fetcher={fetcher}>
      {(d) => (
        <>
          <p className="text-sm text-text-muted">
            Parsed {d.status.parsed} of {d.status.attempted} attempted. {d.status.detail ?? ""}
          </p>
          {d.notifications.length === 0 ? (
            <Empty>No notifications are stored for <EntityName id={ticker} chip={false} />.</Empty>
          ) : (
            <ul className="divide-y divide-border border border-border">
              {d.notifications.map((n) => (
                <li key={n.id} className="space-y-0.5 px-3 py-2 text-sm">
                  <p>
                    <span className="font-medium">{n.person_name ?? "Person unknown"}</span>
                    <span className="text-text-muted">{n.position ? `, ${n.position}` : ""}</span>
                    {n.is_amendment ? <span className="text-text-muted"> (amendment)</span> : null}
                  </p>
                  <p className="text-xs text-text-muted">
                    {n.announced_at ? formatDateTime(n.announced_at) : "Date unknown"}. {n.headline ?? ""} Status: {n.status.replaceAll("_", " ")}
                    {n.status !== "parsed" && n.detail ? `. ${n.detail}` : ""}. {n.transactions.length} transaction{n.transactions.length === 1 ? "" : "s"} parsed.{" "}
                    <a href={n.source_url} className="text-accent-ink hover:underline" rel="noreferrer" target="_blank">
                      Source
                    </a>
                  </p>
                </li>
              ))}
            </ul>
          )}
          <Limits items={d.limitations} />
        </>
      )}
    </ReadPanel>
  );
}

export function EvidenceSummaryPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/evidence-summary", { params: { path: { ticker }, query: {} } }), [ticker]);
  return (
    <ReadPanel id="evidence" kicker="Evidence" title="What the evidence covers" fetcher={fetcher} intro="One line per evidence area with how complete it is, and a list of what is missing. Nothing is filled in for a gap.">
      {(d) => {
        const rec = d as unknown as Rec;
        const sections = Object.entries(rec).filter(([, v]) => isRecord(v) && isRecord(v.coverage));
        const gaps = recs(rec.gaps);
        return (
          <>
            {str(rec.known_as_of) ? <p className="text-xs text-text-muted">Known as of {formatDateTime(String(rec.known_as_of))}</p> : null}
            {sections.length > 0 ? (
              <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                {sections.map(([k, v]) => {
                  const c = (v as Rec).coverage as Rec;
                  return (
                    <li key={k} className="flex justify-between gap-3 border-b border-border-soft py-1">
                      <span>{titleCase(k)}</span>
                      <span className="text-text-muted">{cellText(c.state ?? c.status)}</span>
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {gaps.length > 0 ? (
              <>
                <h3 className="text-sm font-semibold">Gaps</h3>
                <RecordTable rows={gaps} caption="Evidence gaps" max={4} />
              </>
            ) : (
              <p className="text-xs text-text-muted">No gaps reported.</p>
            )}
          </>
        );
      }}
    </ReadPanel>
  );
}

// ---- Reference history -----------------------------------------------------------------------
/** GET /reference/documents/versions, /identity-decisions and /issuers/{id}/as-of: how identity and filings looked over time. */
export function ReferenceHistoryPanel({ ticker }: { ticker: string }) {
  const decisions = useCallback(() => getBrowserApiClient().GET("/reference/identity-decisions"), []);
  const dec = useApiResource(decisions);
  const all = dec.state.kind === "ok" ? dec.state.data : dec.state.kind === "error" ? dec.state.previous : null;
  const mine = useMemo(() => recs(all).filter((r) => !str(r.ticker) || String(r.ticker).toLowerCase() === ticker.toLowerCase()), [all, ticker]);

  const [regulator, setRegulator] = useState("");
  const [externalId, setExternalId] = useState("");
  const [docs, setDocs] = useState<{ rows: Rec[] } | null>(null);
  const [docState, setDocState] = useState<SaveState>({ kind: "idle" });
  const [issuerId, setIssuerId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [issuer, setIssuer] = useState<Rec | null>(null);
  const [issuerState, setIssuerState] = useState<SaveState>({ kind: "idle" });

  async function lookupDocs(e: React.FormEvent) {
    e.preventDefault();
    if (!regulator.trim() || !externalId.trim()) return setDocState({ kind: "error", text: "Enter both the regulator and the document id." });
    setDocState({ kind: "saving" });
    const r = await getBrowserApiClient().GET("/reference/documents/versions", { params: { query: { regulator: regulator.trim(), external_id: externalId.trim() } } });
    if (r.response.ok) {
      setDocs({ rows: recs(r.data) });
      setDocState({ kind: "ok", text: `${recs(r.data).length} version(s) found.` });
    } else setDocState({ kind: "error", text: apiErrorText(r.error, r.response.status).replace("Not saved", "Lookup failed") });
  }
  async function lookupIssuer(e: React.FormEvent) {
    e.preventDefault();
    if (!/^\d+$/.test(issuerId.trim()) || !/^\d{4}-\d{2}-\d{2}$/.test(asOf.trim())) return setIssuerState({ kind: "error", text: "Enter an issuer id and a date as YYYY-MM-DD." });
    setIssuerState({ kind: "saving" });
    const r = await getBrowserApiClient().GET("/reference/issuers/{issuer_id}/as-of", { params: { path: { issuer_id: Number(issuerId) }, query: { date: asOf.trim() } } });
    if (r.response.ok && r.data) {
      setIssuer(r.data as unknown as Rec);
      setIssuerState({ kind: "ok", text: "Loaded." });
    } else setIssuerState({ kind: "error", text: apiErrorText(r.error, r.response.status).replace("Not saved", "Lookup failed") });
  }

  return (
    <Blueprint aria-labelledby="refhist-heading" className="flex min-w-0 flex-col gap-4 px-[22px] py-5">
      <PanelHeading kicker="Reference history" title={<span id="refhist-heading">Identity decisions, filings and issuers over time</span>} />
      <section className="space-y-2">
        <h3 className="text-base font-semibold">Identity decisions</h3>
        {dec.state.kind === "loading" && !all ? <p className="text-sm text-text-muted">Loading…</p> : null}
        {all && mine.length === 0 ? <Empty>No identity decision is recorded for <EntityName id={ticker} chip={false} />.</Empty> : <RecordTable rows={mine} caption="Identity decisions" max={6} />}
        <p className="text-xs text-text-muted">
          Decisions you made carry the <OwnerTag>Entered by you</OwnerTag> mark where the record says who made them.
        </p>
      </section>
      <section className="space-y-2">
        <h3 className="text-base font-semibold">Versions of a filing</h3>
        <form onSubmit={lookupDocs} className="flex flex-wrap items-end gap-3" noValidate>
          <Field label="Regulator" className="w-40"><Input value={regulator} onChange={(e) => setRegulator(e.target.value)} placeholder="e.g. SEC" /></Field>
          <Field label="Document id" className="w-56"><Input value={externalId} onChange={(e) => setExternalId(e.target.value)} /></Field>
          <Button type="submit" size="sm" variant="outline">Show versions</Button>
          <SaveNote state={docState} />
        </form>
        {docs ? (docs.rows.length === 0 ? <Empty>No versions found.</Empty> : <RecordTable rows={docs.rows} caption="Document versions" max={7} />) : null}
      </section>
      <section className="space-y-2">
        <h3 className="text-base font-semibold">Issuer as it stood on a date</h3>
        <form onSubmit={lookupIssuer} className="flex flex-wrap items-end gap-3" noValidate>
          <Field label="Issuer id" className="w-32"><Input inputMode="numeric" value={issuerId} onChange={(e) => setIssuerId(e.target.value)} /></Field>
          <Field label="Date" hint="YYYY-MM-DD" className="w-40"><Input value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field>
          <Button type="submit" size="sm" variant="outline">Show issuer</Button>
          <SaveNote state={issuerState} />
        </form>
        {issuer ? (
          <div className="space-y-2 text-sm">
            <p>
              <span className="font-medium">{str(issuer.legal_name) ?? "Name unknown"}</span>
              <span className="text-text-muted"> as known on {formatDate(String(issuer.known_at ?? issuer.date))}</span>
            </p>
            <RecordTable rows={recs(issuer.aliases)} caption="Names the issuer used" max={4} />
            <RecordTable rows={recs(issuer.relationships)} caption="Predecessor and successor links" max={5} />
          </div>
        ) : null}
      </section>
      <p className="text-xs text-text-muted">
        See also{" "}
        <Link href="/data-sources" className="text-accent-ink hover:underline">
          Data sources
        </Link>{" "}
        for where each record came from.
      </p>
    </Blueprint>
  );
}
