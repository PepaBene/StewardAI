import type { ResearchBody } from "@/lib/api/client";
import { coverageFigures, followUpSummary, groupDiscoveredUrls, roundLine, urlLimit } from "@/lib/research/coverage";
import { sectionLabel } from "@/lib/research/report";

import { NoteList, Panel, Tag } from "./report-bits";

type Props = Pick<ResearchBody, "search_summary" | "discovered_urls" | "rounds" | "stopped_because">;

/** What the web search found, cited and excluded (with the limits), and what follow-up rounds added. */
export function SearchCoverage({ search_summary, discovered_urls, rounds, stopped_because }: Props) {
  const figures = coverageFigures(search_summary, discovered_urls);
  const groups = groupDiscoveredUrls(discovered_urls);
  const followUp = followUpSummary(search_summary, rounds, stopped_because);
  const roundLines = (rounds ?? []).map((r) => roundLine(r, sectionLabel));
  const limitations = search_summary?.limitations ?? [];
  if (!figures && followUp.length === 0 && roundLines.length === 0) return null;
  return (
    <Panel id="search-coverage" title="Search coverage" hint="Every page the searches returned, and what became of it.">
      {figures ? (
        <dl className="grid grid-cols-3 gap-3 text-sm">
          {[
            ["Found", figures.found],
            ["Cited", figures.cited],
            ["Excluded", figures.excluded],
          ].map(([label, n]) => (
            <div key={label}>
              <dt className="text-xs text-text-muted">{label}</dt>
              <dd className="num font-heading text-2xl font-semibold">{n}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <NoteList lines={followUp} />
      <NoteList lines={roundLines} />
      {limitations.length > 0 ? (
        <div>
          <h3 className="text-xs font-medium text-text-muted">Limits</h3>
          <NoteList lines={limitations} />
        </div>
      ) : null}
      {groups
        .filter((g) => g.urls.length > 0)
        .map((g) => (
          <details key={g.status} className="border-t border-border-soft pt-2" open={g.status === "excluded"}>
            <summary className="cursor-pointer text-sm font-medium">
              {g.label} · <span className="num">{g.urls.length}</span>
            </summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[28rem] text-xs">
                <caption className="pb-1 text-left text-text-muted">{g.description}</caption>
                <thead>
                  <tr className="border-b border-border text-left text-xs text-text-muted">
                    <th scope="col" className="py-1 pr-2 font-medium">Page</th>
                    <th scope="col" className="py-1 pr-2 font-medium">Round</th>
                    <th scope="col" className="py-1 pr-2 font-medium">Tier</th>
                    <th scope="col" className="py-1 font-medium">Limit</th>
                  </tr>
                </thead>
                <tbody>
                  {g.urls.map((u) => (
                    <tr key={`${u.round}-${u.url}`} className="border-b border-border-soft align-top last:border-0">
                      <th scope="row" className="py-1 pr-2 text-left font-normal break-words">
                        {u.title || u.host}
                        <span className="block font-mono text-text-muted">{u.host}</span>
                      </th>
                      <td className="num py-1 pr-2">{u.round}</td>
                      <td className="num py-1 pr-2">{typeof u.tier === "number" ? u.tier : "—"}</td>
                      <td className="py-1">
                        {urlLimit(u) ?? <span className="text-text-muted">none</span>}
                        {u.role ? <Tag className="ml-1">{u.role.replaceAll("_", " ")}</Tag> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
    </Panel>
  );
}
