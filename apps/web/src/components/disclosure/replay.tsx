import { datasetCounts, datasetLabel, answerState, type EvidenceAnswer, type ReplayHolding, type ReplayItem } from "@/lib/disclosure/replay";
import { formatDay } from "@/lib/disclosure/model";

function ItemList({ items, empty }: { items: readonly ReplayItem[]; empty: string }) {
  if (items.length === 0) return <p className="text-text-muted">{empty}</p>;
  return (
    <ul className="space-y-1">
      {items.map((i) => (
        <li key={`${i.ref}-${i.label}`}>
          {i.label}
          {i.known_at ? <span className="text-text-muted"> · known <span className="num">{formatDay(i.known_at)}</span></span> : null}
          {i.detail ? <span className="block text-xs text-text-muted">{i.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** One holding: for each dataset, what was known on the date beside what became known afterwards. */
export function ReplayHoldingTable({ holding }: { holding: ReplayHolding }) {
  const counts = datasetCounts(holding);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="sr-only">
          {holding.ticker}: evidence known on the chosen date, and evidence that became known later
        </caption>
        <thead>
          <tr className="border-b border-border text-xs text-text-muted">
            <th scope="col" className="py-2 pr-3 font-medium">Dataset</th>
            <th scope="col" className="py-2 pr-3 font-medium">Known on the date</th>
            <th scope="col" className="py-2 font-medium">Known since (not available then)</th>
          </tr>
        </thead>
        <tbody>
          {counts.map((c) => {
            const d = holding.datasets[c.key];
            return (
              <tr key={c.key} className="border-b border-border-soft align-top">
                <th scope="row" className="py-2 pr-3 font-medium">
                  {datasetLabel(c.key)}
                  <span className="num block text-xs font-normal text-text-muted">{c.known} then · {c.since} since</span>
                </th>
                {d.error ? (
                  <td colSpan={2} className="border border-warning-signal/40 bg-warning-signal-subtle px-2 py-2">{d.error} Nothing is implied about this dataset.</td>
                ) : (
                  <>
                    <td className="py-2 pr-3"><ItemList items={d.known} empty="Nothing known on that date. That is not proof none existed." /></td>
                    <td className="py-2"><ItemList items={d.since} empty="Nothing new has appeared since." /></td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The answer text, what it rests on (source, date, passage) and what it does not cover. */
export function EvidenceAnswerView({ answer }: { answer: EvidenceAnswer }) {
  const state = answerState(answer);
  return (
    <div className="space-y-3 text-sm" aria-live="polite">
      {state === "withheld" ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2">
          The wording was withheld because a number in it was not found in the evidence. The evidence below is unchanged.
        </p>
      ) : (
        <p className="max-w-[68ch]">{answer.text}</p>
      )}
      <p className="text-xs text-text-muted">
        Worded by a fixed template, not a model. {answer.retrieved} verified facts were searched; {answer.evidence.length} matched.
      </p>
      {answer.note ? <p className="text-xs text-text-muted">{answer.note}</p> : null}
      {answer.evidence.length > 0 ? (
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {answer.evidence.map((e) => (
            <li key={`${e.kind}-${e.id}`} className="py-2">
              <p className="font-medium">{e.label}{e.anonymous ? " · counterparty not disclosed" : e.counterparty ? ` · ${e.counterparty}` : ""}</p>
              {e.quote ? <blockquote className="max-w-[68ch] border-l-2 border-border pl-2 text-text-muted">{e.quote}</blockquote> : null}
              <p className="text-xs text-text-muted">
                {e.source} · known <span className="num">{formatDay(e.known_at)}</span>
                {e.document_url ? (
                  <>
                    {" "}
                    <a href={e.document_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Open the filing</a>
                  </>
                ) : null}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
      <ul className="list-disc pl-4 text-xs text-text-muted">
        {answer.limits.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
