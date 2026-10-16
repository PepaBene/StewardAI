"use client";

import { ResourceGate } from "@/components/company/primitives";
import { EntityName } from "@/components/entity-name";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { AREA_LABEL, groupByArea, KIND_LABEL, type ChangeFeed, type ChangeItem } from "@/lib/insights/insights";

import { Notes } from "../xray/shared";

const fetchChanges = () => getBrowserApiClient().GET("/insights/changes", { params: { query: {} } });

export function ChangesView() {
  const { state } = useApiResource(fetchChanges);
  return (
    <ResourceGate state={state} label="the changes">
      {(feed) => <Feed feed={feed} />}
    </ResourceGate>
  );
}

function Feed({ feed }: { feed: ChangeFeed }) {
  const groups = groupByArea(feed.items);
  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-prose text-sm text-text-muted">
        What the app knew at the end of {formatDate(feed.since)} against what it knows now ({formatDate(feed.compared_with)} holdings). Largest share of the portfolio first. A first-time record is labelled New, not Revised.
      </p>
      <Blueprint className="p-4">
        <PanelHeading level={3} title="What was compared" />
        <table className="mt-3 w-full text-sm">
          <caption className="sr-only">Areas compared, records looked at and changes found</caption>
          <thead>
            <tr className="text-left text-xs text-text-muted">
              <th scope="col" className="py-1 font-medium">Area</th>
              <th scope="col" className="py-1 text-right font-medium">Records looked at</th>
              <th scope="col" className="py-1 text-right font-medium">Changes</th>
            </tr>
          </thead>
          <tbody>
            {feed.areas.map((a) => (
              <tr key={a.area} className="border-t border-border-soft align-top">
                <th scope="row" className="py-1.5 text-left font-normal">
                  {AREA_LABEL[a.area]}
                  {a.note ? <div className="text-xs text-text-muted">{a.note}</div> : null}
                </th>
                <td className="num py-1.5 text-right">{a.compared}</td>
                <td className="num py-1.5 text-right">{a.items}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Blueprint>
      {groups.length === 0 ? <p className="text-sm text-text-muted">Nothing differs between the two dates in the areas that were compared.</p> : null}
      {groups.map(([area, items]) => (
        <Area key={area} area={area} items={items} />
      ))}
      <Notes notes={[...feed.not_covered, feed.method]} title="Not compared, and how this works" />
    </div>
  );
}

function Area({ area, items }: { area: ChangeItem["area"]; items: ChangeItem[] }) {
  const shown = items.slice(0, 40);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} title={AREA_LABEL[area]} action={<span className="num text-xs text-text-muted">{items.length} change{items.length === 1 ? "" : "s"}</span>} />
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">{AREA_LABEL[area]}: changes with before and after values and the records behind them</caption>
        <thead>
          <tr className="text-left text-xs text-text-muted">
            <th scope="col" className="py-1 font-medium">Change</th>
            <th scope="col" className="py-1 text-right font-medium">Before</th>
            <th scope="col" className="py-1 text-right font-medium">After</th>
            <th scope="col" className="py-1 text-right font-medium">Portfolio</th>
            <th scope="col" className="py-1 font-medium">Records</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((c, i) => (
            <tr key={`${c.subject}-${c.what}-${i}`} className="border-t border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                <EntityName id={c.subject} chip={false} className="font-semibold" /><span className="text-text-muted">{KIND_LABEL[c.kind]}</span>
                <div className="text-xs text-text-muted">{c.what}</div>
                <div className="text-xs text-text-muted" title={c.relevance_reason}>
                  Relevance {c.relevance ?? 0} of 100
                </div>
              </th>
              <td className="num py-1.5 text-right">{c.before ?? "No earlier record"}</td>
              <td className="num py-1.5 text-right">{c.after ?? "Gone"}</td>
              <td className="num py-1.5 text-right">{c.weight === null || c.weight === undefined ? "Not held" : formatPercent(c.weight, { decimals: 1 })}</td>
              <td className="py-1.5 pl-3 text-xs text-text-muted">
                {[c.before_ref, c.after_ref].map((r, j) =>
                  r ? (
                    <div key={j}>
                      {j === 0 ? "Before" : "After"}: {r.table} #{r.id}
                      {r.known_at ? `, known ${formatDate(r.known_at)}` : ""}
                      {r.url ? (
                        <>
                          {" "}
                          <a href={r.url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                            Source
                          </a>
                        </>
                      ) : null}
                    </div>
                  ) : null,
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length > shown.length ? <p className="mt-2 text-xs text-text-muted">Showing the {shown.length} with the largest portfolio weight of {items.length}.</p> : null}
    </Blueprint>
  );
}
