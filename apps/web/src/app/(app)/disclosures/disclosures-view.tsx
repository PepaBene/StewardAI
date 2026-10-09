"use client";

import { useEffect, useMemo, useState } from "react";

import { CashConversion, DebtMaturities, SegmentBridgePanel, StatementHistory, ValuationGates } from "@/components/disclosure/statements";
import { SAMPLE_MATURITIES, sampleBridge, sampleStatements } from "@/lib/disclosure/fixtures-financials";
import type { SegmentBridge, StatementsOut } from "@/lib/disclosure/financials";
import { CoverageRibbon, DatesTable, EvidencePanel, FilingsTable, ReviewPanel, SourcesPanel, ValuesTable } from "@/components/disclosure/panels";
import { IngestFilings } from "@/components/disclosure/ingest";
import { KnownAtRail } from "@/components/disclosure/known-at-rail";
import { dataOf, Panel, ResourceNotice, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  SAMPLE_CLAIMS,
  SAMPLE_COVERAGE,
  SAMPLE_FILINGS,
  SAMPLE_ISSUER,
  SAMPLE_OBSERVATIONS,
  SAMPLE_REVIEWS,
  SAMPLE_SOURCES,
  SAMPLE_SPANS,
  sampleProvenance,
} from "@/lib/disclosure/fixtures";
import {
  claimsAt,
  filingsAt,
  formatDay,
  observationsAt,
  type Claim,
  type Coverage,
  type Filing,
  type Observation,
  type Provenance,
  type SpanText,
} from "@/lib/disclosure/model";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const SECTIONS = [
  { id: "rail", label: "Known-at timeline" },
  { id: "values", label: "Values and relationships" },
  { id: "statements", label: "Financial statements" },
  { id: "coverage", label: "Coverage" },
  { id: "review", label: "Review queue" },
  { id: "sources", label: "Sources" },
] as const;

const fetchIssuers = () => getBrowserApiClient().GET("/disclosures/issuers");
const fetchSources = () => getBrowserApiClient().GET("/disclosures/sources");
const fetchReviews = () => getBrowserApiClient().GET("/disclosures/review", { params: { query: { status: "open" } } });

type View = { filings: Filing[]; observations: Observation[]; claims: Claim[]; coverage: Coverage[] };
type Evidence = { provenance: Provenance; span: SpanText | null };

/**
 * Public-disclosure evidence browser. The cutoff decides everything on the page: only what was
 * known by that date is listed, so an old decision can be replayed with the knowledge it had.
 * Without live data the page shows a labelled sample so the screen can be judged before any
 * collector is switched on.
 */
export function DisclosuresView() {
  const issuers = useApiResource(fetchIssuers);
  const sources = useApiResource(fetchSources);
  const reviews = useApiResource(fetchReviews);

  const live = dataOf(issuers.state) ?? [];
  // Sample mode only when the fetch succeeded with zero issuers; a failed fetch is an error (shown below), not a sample.
  const sample = issuers.state.kind === "ok" && live.length === 0;
  const settled = issuers.state.kind !== "loading";
  const [issuerId, setIssuerId] = useState<number | null>(null);
  const issuer = sample ? SAMPLE_ISSUER : (live.find((i) => i.issuer_id === issuerId) ?? live[0] ?? null);

  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(until);
  const [selected, setSelected] = useState<number | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [allFilings, setAllFilings] = useState<Filing[]>([]);
  const [liveView, setLiveView] = useState<View | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The rail's picture: every filing known now, fetched once per issuer.
  useEffect(() => {
    if (!issuer || sample) return;
    let cancelled = false;
    getBrowserApiClient()
      .GET("/disclosures/issuers/{issuer_id}/timeline", { params: { path: { issuer_id: issuer.issuer_id } } })
      .then((r) => {
        if (cancelled) return;
        if (r.data) setAllFilings(r.data);
        else setLoadError("Could not load the filings timeline.");
      })
      .catch((e) => !cancelled && setLoadError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [issuer, sample]);

  // What the page shows: re-asked of the server whenever the cutoff settles, so superseded rows come back.
  useEffect(() => {
    if (!issuer || sample) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      getBrowserApiClient()
        .GET("/disclosures/issuers/{issuer_id}/snapshot", { params: { path: { issuer_id: issuer.issuer_id }, query: { as_of: new Date(cutoff).toISOString() } } })
        .then((r) => {
          if (cancelled) return;
          if (r.data) {
            setLoadError(null);
            setLiveView({ filings: r.data.documents, observations: r.data.observations, claims: r.data.claims, coverage: r.data.coverage });
          } else setLoadError("Could not load what was known at that date.");
        })
        .catch((e) => !cancelled && setLoadError(String(e)));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [issuer, sample, cutoff]);

  const [liveStatements, setLiveStatements] = useState<StatementsOut | null>(null);
  const [liveBridge, setLiveBridge] = useState<SegmentBridge | null>(null);

  // Statements as known at the cutoff, and the segment bridge for the latest annual period shown.
  useEffect(() => {
    if (!issuer || sample) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const client = getBrowserApiClient();
        const asOf = new Date(cutoff).toISOString();
        const s = await client.GET("/disclosures/issuers/{issuer_id}/statements", { params: { path: { issuer_id: issuer.issuer_id }, query: { as_of: asOf } } });
        if (cancelled) return;
        if (!s.data) return setLoadError("Could not load the financial statements.");
        setLiveStatements(s.data);
        const end = s.data.history.map((p) => p.period_end).sort().at(-1);
        if (!end) return setLiveBridge(null);
        const b = await client.GET("/disclosures/issuers/{issuer_id}/segment-bridge", { params: { path: { issuer_id: issuer.issuer_id }, query: { period_end: end, as_of: asOf } } });
        if (!cancelled) setLiveBridge(b.data ?? null);
      } catch (e) {
        if (!cancelled) setLoadError(String(e));
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [issuer, sample, cutoff]);

  const statements = sample ? sampleStatements(cutoff) : liveStatements;
  const statementEnd = statements?.history.map((p) => p.period_end).sort().at(-1) ?? null;
  const bridge = sample ? sampleBridge(cutoff) : liveBridge;

  const view: View = useMemo(
    () =>
      sample
        ? {
            filings: filingsAt(SAMPLE_FILINGS, cutoff),
            observations: observationsAt(SAMPLE_OBSERVATIONS, cutoff),
            claims: claimsAt(SAMPLE_CLAIMS, cutoff),
            coverage: SAMPLE_COVERAGE.filter((c) => Date.parse(c.checked_at) <= cutoff),
          }
        : (liveView ?? { filings: [], observations: [], claims: [], coverage: [] }),
    [sample, cutoff, liveView],
  );
  const railFilings = sample ? SAMPLE_FILINGS : allFilings;
  const selectedFiling = view.filings.find((f) => f.version_id === selected) ?? null;

  const openEvidence = async (kind: "observation" | "claim", id: number) => {
      const asOf = new Date(cutoff).toISOString();
      if (sample) {
        const provenance = sampleProvenance(kind, id);
        if (!provenance) return;
        const s = SAMPLE_SPANS[provenance.evidence.evidence_id];
        setEvidence({ provenance, span: s ? { ...s, version_id: provenance.document_version_id, signed_url: null } : null });
        return;
      }
      const client = getBrowserApiClient();
      const p = await client.GET("/disclosures/provenance/{kind}/{row_id}", { params: { path: { kind, row_id: id }, query: { as_of: asOf } } });
      if (!p.data) {
        setLoadError("Could not load the evidence for that row.");
        return;
      }
      const { span_start, span_end } = p.data.evidence;
      const s =
        span_start != null && span_end != null
          ? await client.GET("/disclosures/versions/{version_id}/span", { params: { path: { version_id: p.data.document_version_id }, query: { start: span_start, end: span_end } } })
          : null;
      setEvidence({ provenance: p.data, span: s?.data ?? null });
  };

  const reviewRows = sample ? SAMPLE_REVIEWS : (dataOf(reviews.state) ?? []);
  const sourceRows = sample ? SAMPLE_SOURCES : (dataOf(sources.state) ?? []);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Public disclosures"
        description="What each company had filed, and what we could have known, on any date. Every value links to the sentence it came from."
        aside={
          issuer ? (
            <span>
              {issuer.document_count} documents · latest known {formatDay(issuer.latest_known_at)}
            </span>
          ) : null
        }
      />

      {sample ? (
        <p role="note" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">
          <strong>Sample data.</strong> No issuer has disclosures stored yet, so this page shows an invented company, “Acme Corp”, to demonstrate the screen. Nothing here is real. The
          source list further down is real.
        </p>
      ) : null}

      <ResourceNotice state={issuers.state} what="issuers" />
      {loadError ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          {loadError}
        </p>
      ) : null}

      {!sample && live.length > 1 ? (
        <label className="flex items-center gap-2 text-sm">
          <span className="font-medium">Company</span>
          <select
            value={issuer?.issuer_id ?? ""}
            onChange={(e) => {
              setIssuerId(Number(e.target.value));
              setSelected(null);
              setEvidence(null);
              setLiveView(null);
              setAllFilings([]);
            }}
            className="h-9 border border-border bg-surface-raised px-2 text-sm"
          >
            {live.map((i) => (
              <option key={i.issuer_id} value={i.issuer_id}>
                {i.legal_name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {!sample && issuer ? (
        <Panel id="ingest" title="Ingest filings" description="Fetch this company’s newest annual and quarterly filings now. Each filing’s result, including gaps and failures, is listed.">
          <IngestFilings key={issuer.issuer_id} issuerId={issuer.issuer_id} issuerName={issuer.legal_name} onDone={() => issuers.reload()} />
        </Panel>
      ) : null}

      <SectionNav sections={SECTIONS} />

      <Panel id="rail" title="Known-at timeline" description="Each mark is a filing, placed on the day it became known. Move the cutoff to see exactly what an earlier decision could have used.">
        {issuer ? (
          <>
            <KnownAtRail filings={railFilings} cutoffMs={cutoff} until={until} selectedId={selected} onCutoff={setCutoff} onSelect={setSelected} />
            {selectedFiling ? <DatesTable filing={selectedFiling} /> : <p className="text-sm text-text-muted">Select a filing to see its six dates.</p>}
            <FilingsTable filings={view.filings} selectedId={selected} onSelect={setSelected} />
          </>
        ) : settled ? (
          <p className="text-sm text-text-muted">No company is selected.</p>
        ) : null}
      </Panel>

      <Panel id="values" title="Values and relationships" description="Machine readings show as under review until a person confirms them. A value that is not disclosed is shown as such, never as zero.">
        <ValuesTable observations={view.observations} claims={view.claims} onEvidence={openEvidence} />
        {evidence ? <EvidencePanel provenance={evidence.provenance} span={evidence.span} onClose={() => setEvidence(null)} /> : null}
      </Panel>

      <Panel
        id="statements"
        title="Financial statements"
        description="Figures as each filing first reported them, with later restatements marked. A period whose arithmetic does not check is kept out of valuations, and says why."
      >
        {statements ? (
          <div className="space-y-7">
            <StatementHistory out={statements} />
            <section className="space-y-2" aria-labelledby="cash-heading">
              <h3 id="cash-heading" className="text-base font-semibold">Cash conversion</h3>
              <CashConversion out={statements} end={statementEnd} />
            </section>
            <section className="space-y-2" aria-labelledby="gates-heading">
              <h3 id="gates-heading" className="text-base font-semibold">Can these figures feed a valuation?</h3>
              <ValuationGates out={statements} />
            </section>
            <section className="space-y-2" aria-labelledby="seg-heading">
              <h3 id="seg-heading" className="text-base font-semibold">Segments against the reported total</h3>
              {bridge ? <SegmentBridgePanel bridge={bridge} /> : <p className="text-sm text-text-muted">No segment breakdown is known for the latest period.</p>}
            </section>
            <section className="space-y-2" aria-labelledby="debt-heading">
              <h3 id="debt-heading" className="text-base font-semibold">Debt due by year</h3>
              <DebtMaturities items={sample ? SAMPLE_MATURITIES : []} sample={sample} />
            </section>
          </div>
        ) : settled ? (
          <p className="text-sm text-text-muted">Loading statements…</p>
        ) : null}
      </Panel>

      <Panel id="coverage" title="Coverage" description={`What we could and could not read, as of ${formatDay(new Date(cutoff).toISOString())}. A gap is named, not hidden.`}>
        <CoverageRibbon coverage={view.coverage} />
      </Panel>

      <Panel id="review" title="Review queue" description="Matches and readings a person has to settle. A reason is required for every decision.">
        {!sample ? <ResourceNotice state={reviews.state} what="the review queue" /> : null}
        <ReviewPanel reviews={reviewRows} sample={sample} onDecided={reviews.reload} />
      </Panel>

      <Panel id="sources" title="Sources" description="A source is switched on only after an access test passed. Untested sources stay off.">
        {!sample ? <ResourceNotice state={sources.state} what="sources" /> : null}
        <SourcesPanel sources={sourceRows} />
      </Panel>
    </div>
  );
}
