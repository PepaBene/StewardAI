"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { EntityName } from "@/components/entity-name";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { useEntityNames } from "@/lib/entities/use-entity-names";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { AREA_LABEL, groupByArea, KIND_LABEL, type ChangeFeed, type ChangeItem } from "@/lib/insights/insights";
import { cn } from "@/lib/utils";
import { areaVerdict, managers, MATERIAL_MOVE, readingOrder, relativeChange } from "@/lib/verdicts/changes";

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

const FIRST_ROWS = 12;

function Area({ area, items }: { area: ChangeItem["area"]; items: ChangeItem[] }) {
  const names = useEntityNames();
  const [all, setAll] = useState(false);
  const ordered = readingOrder(area, items);
  const verdict = areaVerdict(
    area,
    items.map((i) => ({ ...i, name: names.get(i.subject) ?? names.get(`${i.subject}_US_EQ`) ?? undefined })),
  );
  const shown = all ? ordered : ordered.slice(0, FIRST_ROWS);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} title={AREA_LABEL[area]} action={<span className="num text-xs text-text-muted">{items.length} change{items.length === 1 ? "" : "s"}</span>} />
      <VerdictLine verdict={verdict.tone} className="mt-2 max-w-4xl">
        {verdict.text}
      </VerdictLine>
      <div className="overflow-x-auto">
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">{AREA_LABEL[area]}: changes with before and after values and the records behind them</caption>
        <thead>
          <tr className="text-left text-xs text-text-muted">
            <th scope="col" className="py-1 font-medium">Change</th>
            <th scope="col" className="py-1 text-right font-medium">Before</th>
            <th scope="col" className="py-1 text-right font-medium">After</th>
            <th scope="col" className="py-1 text-right font-medium">Change</th>
            <th scope="col" className="py-1 text-right font-medium">Portfolio</th>
            <th scope="col" className="py-1 font-medium">Records</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((c, i) => (
            <tr key={`${c.subject}-${c.what}-${i}`} className="border-t border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                <EntityName id={c.subject} chip={false} className="font-semibold" /><span className="text-text-muted">{KIND_LABEL[c.kind]}</span>
                <div className="text-xs text-text-muted" title={`Relevance ${c.relevance ?? 0} of 100: ${c.relevance_reason}`}>
                  {c.what.replace(/_/g, " ")}
                </div>
              </th>
              <td className="num py-1.5 text-right">{c.before ?? "No earlier record"}</td>
              <td className="num py-1.5 text-right">{c.after ?? "Gone"}</td>
              <ChangeCell item={c} />
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
      </div>
      {items.length > FIRST_ROWS ? (
        <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => setAll(!all)}>
          {all ? `Show the first ${FIRST_ROWS}` : `Show all ${items.length}`}
        </Button>
      ) : null}
    </Blueprint>
  );
}

/** The relative move where both sides are numbers; manager counts for 13F rows. */
function ChangeCell({ item }: { item: ChangeItem }) {
  const holders = item.area === "ownership" ? [managers(item.before), managers(item.after)] : null;
  const change = holders ? (holders[0] && holders[1] ? holders[1] / holders[0] - 1 : null) : item.area === "insiders" ? null : relativeChange(item);
  if (change == null || !Number.isFinite(change)) return <td className="py-1.5 text-right text-text-faint">–</td>;
  const material = Math.abs(change) >= MATERIAL_MOVE;
  return (
    <td
      className={cn("num py-1.5 text-right", material ? (change > 0 ? "text-gain" : "text-loss") : "text-text-muted")}
      title={holders ? "Change in the number of 13F managers holding it" : undefined}
    >
      {formatPercent(change, { signed: true, decimals: 1 })}
    </td>
  );
}
