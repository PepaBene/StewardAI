"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { CONFIDENCE_LABEL, pathLabel, SEVERITY_LABEL } from "@/lib/xray/owner-input";

import { Notes } from "../shared";

type Feed = components["schemas"]["EventFeed"];

const fetchFeed = () => getBrowserApiClient().GET("/portfolio/xray/event-feed", { params: { query: { days: 14 } } });

const STATE_LABEL: Record<string, string> = {
  confirmed: "Confirmed by the company or a filing",
  reported: "Reported by the press",
  rumoured: "Rumour",
};

/** Recent news about a company that has supply-chain links to something you hold. Nothing here
 * sends an alert; it shows which stories would meet the high-severity rule. */
export function EventFeed() {
  const { state } = useApiResource(fetchFeed);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="News that reaches you" title="Recent events and where they land" />
      <ResourceGate state={state} label="the event feed">
        {(feed) => <Body feed={feed} />}
      </ResourceGate>
    </Blueprint>
  );
}

function Body({ feed }: { feed: Feed }) {
  return (
    <div className="mt-3 flex flex-col gap-3">
      <p className="max-w-prose text-sm">
        {feed.items.length === 0
          ? `None of the ${feed.stories_read} stories since ${formatDate(feed.since)} is about a company linked to something you hold.`
          : `${feed.items.length} of ${feed.stories_read} stories since ${formatDate(feed.since)} reach something you hold.`}
      </p>
      <ul className="flex flex-col gap-2">
        {feed.items.map((item) => (
          <li key={`${item.story.id}-${item.direction}`} className="border border-border-soft p-3 text-sm">
            <p className="font-medium">
              <a href={item.story.url} target="_blank" rel="noreferrer" className="hover:underline">
                {item.story.headline}
              </a>
            </p>
            <p className="text-xs text-text-muted">
              {item.story.ticker.split("_")[0]}, {formatDate(item.story.published_at)}, {STATE_LABEL[item.state] ?? item.state}
            </p>
            <p className="mt-1">
              {item.direction === "downstream" ? "If a supplier is hit:" : "If a customer is hit:"} {pathLabel(item.top)}, {formatPercent(item.impact.reached_weight, { decimals: 1 })} of the portfolio reached,{" "}
              {CONFIDENCE_LABEL[item.top.confidence].toLowerCase()}, {SEVERITY_LABEL[item.top.severity].toLowerCase()}.
              {item.would_alert ? " This meets the rule for a high-severity alert." : ""}
            </p>
          </li>
        ))}
      </ul>
      <Notes notes={[feed.note]} title="How this is built" />
    </div>
  );
}
