"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Sparkline } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { FilterChips, type FilterChip } from "@/components/ui/filter-chips";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { label } from "@/lib/labels";
import {
  cadenceOf,
  formatChange,
  formatLevel,
  formatPeriod,
  GEOGRAPHY_NAMES,
  GEOGRAPHY_ORDER,
  geographyKey,
  previousLabel,
  rangePosition,
  standing,
  unitSuffix,
  type MacroRange,
} from "@/lib/macro/dashboard";
import { groupMacro } from "@/lib/macro/groups";
import { cn } from "@/lib/utils";

type Card = components["schemas"]["MacroCard"];

/**
 * Every stored macro series as a small card: the latest value, how it moved on the previous
 * reading and on a year, a five-year line, and a ruler showing where it sits in its 1-, 5- and
 * 10-year range. Grouped by what the series measures; filtered by place.
 */
export function MacroDashboard() {
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/macro/dashboard"),
    [],
  );
  const { state } = useApiResource(fetcher);
  const cards = useMemo(
    () =>
      state.kind === "ok"
        ? state.data
        : state.kind === "error"
          ? (state.previous ?? [])
          : [],
    [state],
  );
  const [place, setPlace] = useState<string>("all");

  const chips = useMemo<FilterChip<string>[]>(() => {
    const counts = new Map<string, number>();
    for (const c of cards)
      counts.set(
        geographyKey(c.geography),
        (counts.get(geographyKey(c.geography)) ?? 0) + 1,
      );
    return [
      { key: "all", label: "Everywhere", count: cards.length },
      ...GEOGRAPHY_ORDER.filter((g) => counts.has(g)).map((g) => ({
        key: g,
        label: GEOGRAPHY_NAMES[g],
        count: counts.get(g),
      })),
    ];
  }, [cards]);
  const shown =
    place === "all"
      ? cards
      : cards.filter((c) => geographyKey(c.geography) === place);
  const latest = cards.reduce<string | null>(
    (max, c) => (max === null || c.latest_period > max ? c.latest_period : max),
    null,
  );

  return (
    <section aria-labelledby="macro-dashboard-heading" className="space-y-5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2
          id="macro-dashboard-heading"
          className="text-[22px] leading-tight font-semibold"
        >
          Where the economy stands
        </h2>
        {latest ? (
          <p className="text-sm text-text-muted">{`${cards.length} series, newest reading ${formatPeriod(latest, "daily")}`}</p>
        ) : null}
      </div>
      {chips.length > 2 ? (
        <FilterChips
          chips={chips}
          value={place}
          onChange={setPlace}
          label="Show series for"
        />
      ) : null}

      {state.kind === "loading" ? (
        <p
          className="flex items-center gap-2 text-sm text-text-muted"
          role="status"
        >
          <LoaderCircleIcon
            aria-hidden="true"
            className="size-4 animate-spin"
          />
          Loading series…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={`Could not load the macro series: ${state.title}`}>
          {state.detail}
        </AlertMessage>
      ) : null}
      {state.kind === "ok" && cards.length === 0 ? (
        <EmptyState title="No macro series stored yet">
          The macro job fills these from FRED, the ECB, Eurostat and the Czech
          National Bank. Run it from{" "}
          <Link href="/jobs" className="text-accent-ink hover:underline">
            Jobs
          </Link>
          .
        </EmptyState>
      ) : null}

      {groupMacro(shown, (c) => c.series_id.split(":")[0]).map(
        ({ group, items }) => (
          <section
            key={group}
            aria-labelledby={`macro-group-${group}`}
            className="space-y-2.5"
          >
            <h3
              id={`macro-group-${group}`}
              className="text-[15px] font-semibold"
            >
              {group}
            </h3>
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {items.map((c) => (
                <MacroCardView
                  key={`${c.source}:${c.series_id}`}
                  card={c}
                  showPlace={place === "all"}
                />
              ))}
            </ul>
          </section>
        ),
      )}
    </section>
  );
}

function titleOf(card: Card): string {
  const named = label("macro", card.series_id);
  // A mapped key gives a plain title; otherwise the stored label (Eurostat's are per country).
  return named !== card.series_id && !named.includes(":") ? named : card.label;
}

function MacroCardView({
  card,
  showPlace,
}: {
  card: Card;
  showPlace: boolean;
}) {
  const points = card.spark.map((p) => Number(p.value));
  const periods = card.spark.map((p) => p.period);
  const cadence = cadenceOf(periods);
  const value = Number(card.latest_value);
  const prev =
    card.previous_value === null || card.previous_value === undefined
      ? null
      : Number(card.previous_value);
  const yearAgo =
    card.year_ago_value === null || card.year_ago_value === undefined
      ? null
      : Number(card.year_ago_value);
  const ranges: MacroRange[] = card.ranges.map((r) => ({
    years: r.years,
    low: Number(r.low),
    high: Number(r.high),
    percentile: r.percentile,
    readings: r.readings,
  }));
  const onPrev = formatChange(prev, value, card.unit);
  const onYear = formatChange(yearAgo, value, card.unit);
  const title = titleOf(card);
  const place = GEOGRAPHY_NAMES[card.geography] ?? card.geography;
  const words = standing(ranges);

  return (
    <li className="flex flex-col gap-3 rounded-card bg-surface-muted px-4 pt-3.5 pb-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm leading-snug font-semibold">{title}</h4>
          <p className="text-xs text-text-muted">
            {showPlace && !title.startsWith(place.split(" ")[0])
              ? `${place}, `
              : ""}
            {formatPeriod(card.latest_period, cadence)}
          </p>
        </div>
        <p className="num shrink-0 text-right text-[26px] leading-none font-semibold tracking-tight">
          {formatLevel(value, card.unit)}
          <span className="text-[13px] font-normal text-text-muted">
            {unitSuffix(card.unit)}
          </span>
        </p>
      </div>

      <dl className="flex gap-5 text-xs">
        {onPrev ? (
          <div>
            <dt className="text-text-muted">{previousLabel(cadence)}</dt>
            <dd className="num font-medium">{onPrev}</dd>
          </div>
        ) : null}
        {onYear ? (
          <div>
            <dt className="text-text-muted">on a year</dt>
            <dd className="num font-medium">{onYear}</dd>
          </div>
        ) : null}
      </dl>

      <Sparkline
        values={points}
        area
        tone="accent"
        width={240}
        height={48}
        className="h-12"
        label={`${title}, last ${points.length} readings`}
        formatValue={(v) => formatLevel(v, card.unit)}
      />

      {ranges.length > 0 ? (
        <RangeRuler ranges={ranges} value={value} unit={card.unit} />
      ) : null}
      {words ? (
        <p className="text-[13px] text-text">{words}</p>
      ) : (
        <p className="text-[13px] text-text-muted">
          Too little history stored to place it in a range.
        </p>
      )}
      <p className="mt-auto text-[11px] text-text-faint">
        {card.source_url ? (
          <a
            href={card.source_url}
            target="_blank"
            rel="noreferrer"
            className="hover:underline"
          >
            {label("provider", card.source)}
          </a>
        ) : (
          label("provider", card.source)
        )}
      </p>
    </li>
  );
}

/**
 * The longest window's low-to-high as the track; the shorter windows as nested darker bands, and
 * the latest value as a mark. Ends are labelled with the longest window's low and high.
 */
function RangeRuler({
  ranges,
  value,
  unit,
}: {
  ranges: readonly MacroRange[];
  value: number;
  unit: string;
}) {
  const sorted = [...ranges].sort((a, b) => b.years - a.years);
  const outer = sorted[0];
  const at = rangePosition(value, outer.low, outer.high);
  const shade = ["bg-accent/10", "bg-accent/20", "bg-accent/35"];
  return (
    <figure className="space-y-1">
      <div
        className="relative h-3"
        role="img"
        aria-label={
          sorted
            .map(
              (r) =>
                `${r.years}-year range ${formatLevel(r.low, unit)} to ${formatLevel(r.high, unit)}`,
            )
            .join("; ") + `; latest ${formatLevel(value, unit)}`
        }
      >
        <div className="absolute inset-x-0 top-1 h-1 rounded-full bg-border" />
        {sorted.map((r, i) => {
          const lo = rangePosition(r.low, outer.low, outer.high);
          const hi = rangePosition(r.high, outer.low, outer.high);
          return (
            <div
              key={r.years}
              className={cn(
                "absolute top-1 h-1 rounded-full",
                shade[i] ?? shade[2],
              )}
              style={{
                left: `${lo * 100}%`,
                width: `${Math.max(0.5, (hi - lo) * 100)}%`,
              }}
            />
          );
        })}
        <div
          className="absolute top-0 h-3 w-[3px] -translate-x-1/2 rounded-sm bg-accent"
          style={{ left: `${at * 100}%` }}
        />
      </div>
      <figcaption className="num flex justify-between text-[11px] text-text-faint">
        <span>{formatLevel(outer.low, unit)}</span>
        <span>{`${sorted.map((r) => `${r.years}y`).join(" / ")} range`}</span>
        <span>{formatLevel(outer.high, unit)}</span>
      </figcaption>
    </figure>
  );
}
