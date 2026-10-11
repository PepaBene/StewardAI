import type { ResearchBody } from "@/lib/api/client";
import { stopRuleLines, toolFreshness, toolLabel, toolCallCounts, toolReason, toolStatusLabel } from "@/lib/research/coverage";
import { sectionLabel } from "@/lib/research/report";

import { NoteList, Panel, Tag } from "./report-bits";

/** The plan the run followed: questions, stored-data tools, web search intents and the stop rule. */
export function PlanPanel({ plan }: { plan: ResearchBody["plan"] }) {
  if (!plan) return null;
  return (
    <Panel id="research-plan" title="Research plan" hint={plan.question}>
      <ol className="space-y-2 text-sm">
        {plan.questions.map((q) => (
          <li key={q.id} className="border-t border-border-soft pt-2">
            <p>{q.question}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-text-muted">
              {q.section ? <Tag>{sectionLabel(q.section)}</Tag> : <Tag>whole report</Tag>}
              {q.internal_tools.map((t) => (
                <Tag key={t} className="font-mono">{t}</Tag>
              ))}
              {q.web_intent ? <span>Web: {q.web_intent}</span> : null}
            </p>
          </li>
        ))}
      </ol>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-text-muted">Stored-data tools</dt>
          <dd>{plan.internal_tools.length > 0 ? plan.internal_tools.map(toolLabel).join(", ") : "none"}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Web search intents</dt>
          <dd>{plan.web_intents.length > 0 ? plan.web_intents.join("; ") : "none"}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Preferred source tiers</dt>
          <dd className="num">{plan.preferred_tiers.length > 0 ? plan.preferred_tiers.join(", ") : "any"}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Counter-evidence</dt>
          <dd>{plan.counter_evidence_intent ?? "not planned"}</dd>
        </div>
      </dl>
      <div>
        <h3 className="text-xs font-medium text-text-muted">Stop rule</h3>
        <NoteList lines={stopRuleLines(plan.stop)} />
      </div>
    </Panel>
  );
}

/** The stored-data tool calls of the run: status, freshness and why a call was skipped or failed. */
export function ToolCalls({ calls }: { calls: ResearchBody["tool_calls"] }) {
  if (!calls || calls.length === 0) return null;
  const counts = toolCallCounts(calls);
  return (
    <Panel id="tool-calls" title={`Stored-data tool calls · ${calls.length}`}>
      <p className="flex flex-wrap gap-1.5 text-xs">
        {counts.map((c) => (
          <Tag key={c.status}>
            {c.label} <span className="num">{c.count}</span>
          </Tag>
        ))}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] text-xs">
          <caption className="sr-only">Stored-data tool calls: status, freshness and the reason a call was skipped or failed</caption>
          <thead>
            <tr className="border-b border-border text-left text-[11px] tracking-wide text-text-muted uppercase">
              <th scope="col" className="py-1 pr-2 font-medium">Tool</th>
              <th scope="col" className="py-1 pr-2 font-medium">Status</th>
              <th scope="col" className="py-1 font-medium">Freshness and reason</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c, i) => (
              <tr key={`${c.tool}-${i}`} className="border-b border-border-soft align-top last:border-0">
                <th scope="row" className="py-1 pr-2 text-left font-mono font-normal">{c.tool}</th>
                <td className="py-1 pr-2">{toolStatusLabel(c.status)}</td>
                <td className="py-1">
                  {[toolFreshness(c), toolReason(c)].filter(Boolean).join(" · ") || <span className="text-text-muted">no detail</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
