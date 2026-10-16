import { sectionLabel } from "@/lib/research/report";
import { formatCost, formatMs, outcomeLabel, traceOf, traceTotals, validationLine } from "@/lib/research/trace";

import { NoteList, Panel, Tag } from "./report-bits";

/**
 * The run's trace: each model call (tokens, cost, time, outcome), the web queries and URLs, what
 * was offered to each section against what it cited, and what output validation dropped. Metadata
 * only; reports from before the trace, and purged ones, show nothing.
 */
export function TracePanel({ body }: { body: unknown }) {
  const trace = traceOf(body);
  if (!trace || trace.model_calls.length === 0) return null;
  const checked = trace.validation.filter((v) => validationLine(v) !== "");
  const searched = trace.web.filter((w) => (w.queries?.length ?? 0) > 0 || (w.urls_found ?? 0) > 0);
  return (
    <Panel
      id="run-trace"
      title="Run trace"
      hint={trace.plan_source ? `Plan written by ${trace.plan_source}. No prompts or page text are kept.` : "No prompts or page text are kept."}
    >
      <NoteList lines={traceTotals(trace)} />

      <details>
        <summary className="cursor-pointer text-xs font-medium text-text-muted">Model calls</summary>
        <div className="mt-1 overflow-x-auto">
          <table className="w-full min-w-[32rem] text-xs">
            <caption className="sr-only">Model calls: tokens, cost, time and outcome</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-text-muted">
                <th scope="col" className="py-1 pr-2 font-medium">Step</th>
                <th scope="col" className="py-1 pr-2 font-medium">Model</th>
                <th scope="col" className="num py-1 pr-2 text-right font-medium">Tokens in / out</th>
                <th scope="col" className="num py-1 pr-2 text-right font-medium">Cost</th>
                <th scope="col" className="num py-1 pr-2 text-right font-medium">Time</th>
                <th scope="col" className="py-1 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {trace.model_calls.map((c, i) => (
                <tr key={`${c.step}-${i}`} className="border-b border-border-soft align-top last:border-0">
                  <th scope="row" className="py-1 pr-2 text-left font-mono font-normal">{c.step}</th>
                  <td className="py-1 pr-2 font-mono">{c.model}</td>
                  <td className="num py-1 pr-2 text-right">
                    {(c.input_tokens ?? 0).toLocaleString("en")} / {(c.output_tokens ?? 0).toLocaleString("en")}
                  </td>
                  <td className="num py-1 pr-2 text-right">{formatCost(c.cost_usd)}</td>
                  <td className="num py-1 pr-2 text-right">{formatMs(c.ms)}</td>
                  <td className="py-1">
                    {outcomeLabel(c.outcome)}
                    {c.retries ? <span className="block text-text-muted">{c.retries} retries</span> : null}
                    {c.note ? <span className="block text-text-muted">{c.note}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      {searched.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-text-muted">Web searches · {searched.length}</summary>
          <ul className="mt-1 space-y-2 text-xs">
            {searched.map((w, i) => (
              <li key={`${w.step}-${i}`} className="border-t border-border-soft pt-1">
                <p className="flex flex-wrap items-center gap-1">
                  <Tag className="font-mono">{w.step}</Tag>
                  <span className="num">{w.urls_found ?? 0}</span> found, <span className="num">{w.urls_cited ?? 0}</span> cited,
                  <span className="num">{w.urls_excluded ?? 0}</span> excluded
                </p>
                {(w.queries ?? []).map((q) => (
                  <p key={q} className="text-text-muted">Query: {q}</p>
                ))}
                {(w.opened ?? []).slice(0, 4).map((u) => (
                  <p key={u} className="break-all text-text-muted">Cited: {u}</p>
                ))}
                {(w.errors ?? []).map((e) => (
                  <p key={e} className="text-text-muted">Search error: {e}</p>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {trace.retrieval.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-text-muted">Sources offered against cited</summary>
          <ul className="mt-1 space-y-0.5 text-xs">
            {trace.retrieval.map((r) => (
              <li key={r.section}>
                {sectionLabel(r.section)}: <span className="num">{r.sources_offered}</span> offered,{" "}
                <span className="num">{r.sources_cited}</span> cited
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {checked.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-text-muted">Output validation · {checked.length}</summary>
          <ul className="mt-1 space-y-0.5 text-xs">
            {checked.map((v, i) => (
              <li key={`${v.step}-${i}`}>
                {v.section ? sectionLabel(v.section) : v.step}: {validationLine(v)}
                {(v.check_reasons ?? []).map((r) => (
                  <span key={r} className="block text-text-muted">{r}</span>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Panel>
  );
}
