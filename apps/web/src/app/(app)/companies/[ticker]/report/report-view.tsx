"use client";

import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate, SourceLink } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatRatio, toNumber, type CompanyReport, type ReportVersionSummary } from "@/lib/company/company";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

import { RubricBody } from "../assessment/assessment-view";

const COMPACT = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });
const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });

function figure(value: string | number | null, unit: string): string | null {
  const n = toNumber(value);
  if (n === null) return null;
  return unit === "money" ? COMPACT.format(n) : formatRatio(n, unit);
}

function Body({ report, conclusion, onConclusion, readOnly }: { report: CompanyReport; conclusion: string; onConclusion: (v: string) => void; readOnly: boolean }) {
  const sources = new Map((report.research?.body.sources ?? []).map((s) => [s.id, s]));
  return (
    <div className="flex flex-col gap-5">
      <Blueprint aria-labelledby="rep-figures" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker={`${report.name ?? report.ticker} · as of ${report.as_of}`} title={<span id="rep-figures">Key figures</span>} />
        {report.key_figure_warnings.map((w) => (
          <AlertMessage key={w} title="Note">
            {w}
          </AlertMessage>
        ))}
        <div className="overflow-x-auto border border-border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-surface-muted text-xs">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Figure</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Period</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Value</th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Own 5-year median</th>
              </tr>
            </thead>
            <tbody>
              {report.key_figures.map((k) => (
                <tr key={k.id} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1 text-left font-normal" title={k.formula}>
                    {k.label}
                  </th>
                  <td className="px-2 py-1 text-right text-xs text-text-muted">{k.period ?? ""}</td>
                  <td className="num px-2 py-1 text-right">{figure(k.value, k.unit) ?? <Absent reason={k.reason} />}</td>
                  <td className="num px-2 py-1 text-right text-text-muted">{figure(k.own_median_5y, k.unit) ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-text-muted">Every figure is computed by the app from filed statements; hover a label for its formula.</p>
      </Blueprint>

      {report.segments.length > 0 ? (
        <Blueprint aria-labelledby="rep-segments" className="space-y-2 px-[22px] py-5">
          <PanelHeading kicker="Reported, latest period" title={<span id="rep-segments">Revenue mix</span>} />
          {report.segments.map((s) => (
            <div key={s.breakdown} className="text-sm">
              <p className="font-medium">
                {s.breakdown}
                {s.period ? <span className="ml-2 text-xs font-normal text-text-muted">{s.period}</span> : null}
              </p>
              {s.note ? <p className="text-xs text-text-muted">{s.note}</p> : null}
              <ul className="mt-1 grid gap-x-8 sm:grid-cols-2">
                {s.lines.map((line) => {
                  const label = String(line[0]);
                  const share = toNumber(line[1]);
                  return (
                    <li key={label} className="flex justify-between gap-3 border-t border-border-soft py-0.5">
                      <span>{label}</span>
                      <span className="num">{share === null ? <Absent reason="share not computable" /> : `${(share * 100).toFixed(1)}%`}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </Blueprint>
      ) : null}

      <RubricBody rubric={report.rubric} />

      <Blueprint aria-labelledby="rep-research" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker={report.research ? `Written ${when(report.research.created_at)}` : "Research"} title={<span id="rep-research">Latest verified research</span>} />
        {!report.research ? (
          <p className="text-sm text-text-muted">No verified research report is stored for this company yet. Run one from the research page and it will appear here with its citations.</p>
        ) : (
          <>
            {report.research.older_than_latest_filing ? (
              <AlertMessage title="Older than the latest filing">A newer filing has arrived since this research was written. Re-run it before relying on its figures.</AlertMessage>
            ) : null}
            <p className="text-sm font-medium">{report.research.body.headline}</p>
            {report.research.body.sections.map((sec) => (
              <div key={sec.id} className="space-y-1">
                <h3 className="text-sm font-semibold">{sec.title}</h3>
                <p className="text-sm leading-relaxed">
                  {sec.sentences.map((s, i) => (
                    <span key={i} className={cn(s.check === "unsupported" && "text-loss")}>
                      {s.text}
                      {(s.source_ids ?? []).map((id) => (
                        <sup key={id} className="ml-0.5 text-xs text-accent-ink">
                          [{id}]
                        </sup>
                      ))}{" "}
                    </span>
                  ))}
                </p>
              </div>
            ))}
            {sources.size > 0 ? (
              <ol className="space-y-0.5 border-t border-border-soft pt-2 text-xs text-text-muted">
                {[...sources.values()].map((s) => (
                  <li key={s.id}>
                    [{s.id}] {s.url ? <SourceLink href={s.url}>{s.title}</SourceLink> : s.title}
                  </li>
                ))}
              </ol>
            ) : null}
          </>
        )}
      </Blueprint>

      {report.case_items.length > 0 ? (
        <Blueprint aria-labelledby="rep-case" className="space-y-2 px-[22px] py-5">
          <PanelHeading kicker="Your cited items" title={<span id="rep-case">Investment case</span>} />
          <ul className="space-y-1 text-sm">
            {report.case_items.map((c, i) => (
              <li key={String(c.id ?? i)}>
                <span className="text-text-muted">{String(c.kind ?? "")}: </span>
                {String(c.statement ?? "")}
                <span className="ml-2 text-xs text-text-muted">
                  {String(c.status ?? "")} · {String(c.confidence ?? "")} confidence
                </span>
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}

      <Blueprint aria-labelledby="rep-conflicts" className="space-y-2 px-[22px] py-5">
        <PanelHeading kicker="Where the parts disagree" title={<span id="rep-conflicts">Conflicts to review</span>} />
        {report.conflicts.length === 0 ? (
          <p className="text-sm text-text-muted">The checks found no conflict between the figures, the research, your case and the relationship evidence.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {report.conflicts.map((c) => (
              <li key={`${c.area}-${c.text}`} className="flex gap-2">
                <span className={cn("w-16 shrink-0 font-semibold", c.severity === "review" ? "text-unconfirmed" : "text-text-muted")}>{c.severity === "review" ? "Review" : "Note"}</span>
                <span>
                  <span className="text-text-muted">{c.area}: </span>
                  {c.text}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Blueprint>

      <Blueprint aria-labelledby="rep-conclusion" className="space-y-2 px-[22px] py-5">
        <PanelHeading kicker="Your words" title={<span id="rep-conclusion">Conclusion</span>} />
        <textarea
          value={conclusion}
          onChange={(e) => onConclusion(e.target.value)}
          readOnly={readOnly}
          rows={6}
          maxLength={8000}
          aria-labelledby="rep-conclusion"
          placeholder="What you conclude and why, in your own words."
          className="w-full border border-border bg-surface px-2 py-1.5 text-sm"
        />
        <p className="font-mono text-xs text-text-muted">
          Method {report.method_version} · input hash {report.input_hash.slice(0, 16)}
        </p>
      </Blueprint>
    </div>
  );
}

export function ReportView({ ticker }: { ticker: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const current = useApiResource(useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/report", { params: { path: { ticker } } }), [ticker]));
  const versions = useApiResource(useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/report/versions", { params: { path: { ticker } } }), [ticker]));
  const past = useApiResource(
    useCallback(
      () =>
        open === null
          ? Promise.resolve({ data: undefined, error: undefined, response: new Response() })
          : getBrowserApiClient().GET("/companies/{ticker}/report/versions/{number}", { params: { path: { ticker, number: open } } }),
      [ticker, open],
    ),
  );

  const stored = current.state.kind === "ok" ? current.state.data.conclusion : "";
  const conclusion = draft ?? stored;

  async function save() {
    setSaving(true);
    setSaveError(null);
    const { error } = await getBrowserApiClient().POST("/companies/{ticker}/report", { params: { path: { ticker } }, body: { conclusion } });
    setSaving(false);
    if (error) {
      setSaveError("The report could not be saved. Try again.");
      return;
    }
    setDraft(null);
    versions.reload();
    current.reload();
  }

  const list: ReportVersionSummary[] = versions.state.kind === "ok" ? versions.state.data : [];

  return (
    <div className="grid items-start gap-5 xl:grid-cols-[1fr_18rem]">
      <div className="min-w-0 space-y-5">
        {open === null ? (
          <ResourceGate state={current.state} label="the report">
            {(report: CompanyReport) => (
              <>
                <Body report={report} conclusion={conclusion} onConclusion={setDraft} readOnly={false} />
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" onClick={save} disabled={saving} className="border border-border px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50">
                    Save as a new version
                  </button>
                  <span className="text-xs text-text-muted">Saving keeps the figures, research and your conclusion as they are now. Earlier versions are never changed.</span>
                  {saveError ? (
                    <p role="alert" className="w-full text-sm text-loss">
                      {saveError}
                    </p>
                  ) : null}
                </div>
              </>
            )}
          </ResourceGate>
        ) : (
          <ResourceGate state={past.state} label={`version ${open}`}>
            {(v) =>
              v ? (
                <>
                  <AlertMessage title={`Version ${open}, saved ${when(v.created_at)}`}>
                    This is the report as it was saved, including figures that may have changed since.{" "}
                    <button type="button" onClick={() => setOpen(null)} className="text-accent-ink underline">
                      Back to the current report
                    </button>
                  </AlertMessage>
                  <Body report={v.report} conclusion={v.report.conclusion} onConclusion={setDraft} readOnly />
                </>
              ) : null
            }
          </ResourceGate>
        )}
      </div>

      <Blueprint aria-labelledby="rep-versions" className="space-y-2 px-[22px] py-5">
        <PanelHeading kicker="Saved reports" title={<span id="rep-versions">Versions</span>} />
        {list.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing saved yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {list.map((v) => (
              <li key={v.version}>
                <button type="button" onClick={() => setOpen(v.version)} className={cn("w-full border border-border px-2 py-1.5 text-left hover:border-accent", open === v.version && "bg-surface-muted")}>
                  <span className="font-medium">Version {v.version}</span>
                  <span className="block text-xs text-text-muted">{when(v.created_at)}</span>
                  {v.conclusion_excerpt ? <span className="mt-1 block text-xs">{v.conclusion_excerpt}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Blueprint>
    </div>
  );
}
