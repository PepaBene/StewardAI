"use client";

import { CalendarDaysIcon, ChevronLeftIcon, ChevronRightIcon, ListIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { formatDay, MONTH_SHORT, parseDay } from "@/lib/charts/time";
import { groupByDate, monthGrid } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F22 MonthCalendar: a Monday-first month grid with typed dots (one shape and colour per event
 * type, always explained by the legend) and chips naming the ticker and amount. Below md the grid
 * collapses to an agenda list by default, with "Open calendar" to switch. Every event is a
 * focusable chip, so the keyboard reaches each one.
 */

export type CalendarKind = { key: string; label: string; dot: string; hollow?: boolean };
export type CalendarItem = { key: string; date: string; kind: string; title: string; amount?: string | null; note?: string | null };

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Dot({ kind }: { kind: CalendarKind | undefined }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-2 shrink-0 rounded-full", kind?.hollow ? "border-[1.5px] bg-transparent" : "", kind?.dot ?? "bg-text-faint")}
    />
  );
}

export function MonthCalendar({
  month,
  firstDay,
  items,
  kinds,
  onMonth,
  today,
  footer,
}: {
  /** "2026-10". */
  month: string;
  firstDay: string;
  items: readonly CalendarItem[];
  kinds: readonly CalendarKind[];
  onMonth: (delta: number) => void;
  today?: string;
  footer?: ReactNode;
}) {
  const [view, setView] = useState<"auto" | "grid" | "list">("auto");
  const byDate = groupByDate(items);
  const kindOf = new Map(kinds.map((k) => [k.key, k]));
  const d = parseDay(firstDay);
  const title = d ? `${MONTH_SHORT[d.month]} ${d.year}` : month;
  const sorted = [...items].sort((a, b) => a.date.localeCompare(b.date));

  const grid = (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] table-fixed border-collapse text-xs">
        <caption className="sr-only">Events in {title}</caption>
        <thead>
          <tr>
            {WEEKDAYS.map((w) => (
              <th key={w} scope="col" className="bg-surface-muted py-1 text-center font-medium text-text-muted">
                {w}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {monthGrid(firstDay).map((week) => (
            <tr key={week[0].iso}>
              {week.map((day) => {
                const events = byDate.get(day.iso) ?? [];
                return (
                  <td key={day.iso} className={cn("h-24 border border-border-soft p-1 align-top", !day.inMonth && "bg-surface-muted/40")}>
                    <div className={cn("num mb-1 text-[11px]", day.inMonth ? "text-text-muted" : "text-text-faint", day.iso === today && "font-semibold text-accent-ink")}>
                      {day.day}
                      {day.iso === today ? <span className="sr-only"> (today)</span> : null}
                    </div>
                    <ul className="space-y-0.5">
                      {events.slice(0, 4).map((e) => (
                        <li key={e.key}>
                          <span
                            tabIndex={0}
                            title={`${formatDay(e.date)} · ${kindOf.get(e.kind)?.label ?? e.kind} · ${e.title}${e.amount ? ` · ${e.amount}` : ""}${e.note ? ` · ${e.note}` : ""}`}
                            className="flex items-center gap-1 truncate focus-visible:outline-2 focus-visible:outline-accent"
                          >
                            <Dot kind={kindOf.get(e.kind)} />
                            <span className="truncate font-medium">{e.title}</span>
                            {e.amount ? <span className="num truncate text-text-muted">{e.amount}</span> : null}
                            <span className="sr-only">, {kindOf.get(e.kind)?.label ?? e.kind}</span>
                          </span>
                        </li>
                      ))}
                      {events.length > 4 ? <li className="text-text-faint">+{events.length - 4} more</li> : null}
                    </ul>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const list = (
    <ul className="divide-y divide-border-soft border border-border">
      {sorted.length === 0 ? <li className="p-3 text-sm text-text-faint">No events this month.</li> : null}
      {sorted.map((e) => (
        <li key={e.key} tabIndex={0} className="flex items-center gap-3 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-accent">
          <span className="num w-14 shrink-0 text-xs text-text-muted">{formatDay(e.date).replace(/ \d{4}$/, "")}</span>
          <Dot kind={kindOf.get(e.kind)} />
          <span className="min-w-0 flex-1">
            <span className="font-medium">{e.title}</span> <span className="text-text-muted">{kindOf.get(e.kind)?.label ?? e.kind}</span>
            {e.note ? <span className="block text-xs text-text-faint">{e.note}</span> : null}
          </span>
          {e.amount ? <span className="num shrink-0">{e.amount}</span> : null}
        </li>
      ))}
    </ul>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={() => onMonth(-1)} aria-label="Previous month">
            <ChevronLeftIcon aria-hidden="true" />
          </Button>
          <h3 className="min-w-24 text-center text-base font-semibold" aria-live="polite">
            {title}
          </h3>
          <Button variant="ghost" size="icon" onClick={() => onMonth(1)} aria-label="Next month">
            <ChevronRightIcon aria-hidden="true" />
          </Button>
        </div>
        <div className="flex gap-1">
          <Button variant={view === "list" ? "outline" : "ghost"} size="sm" onClick={() => setView("list")} className={cn(view === "auto" && "md:hidden")}>
            <ListIcon aria-hidden="true" />
            List
          </Button>
          <Button variant={view === "grid" ? "outline" : "ghost"} size="sm" onClick={() => setView("grid")}>
            <CalendarDaysIcon aria-hidden="true" />
            {view === "auto" ? <span className="md:hidden">Open calendar</span> : "Calendar"}
            {view === "auto" ? <span className="hidden md:inline">Calendar</span> : null}
          </Button>
        </div>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
        {kinds.map((k) => (
          <li key={k.key} className="inline-flex items-center gap-1.5">
            <Dot kind={k} />
            {k.label}
          </li>
        ))}
      </ul>
      {view === "grid" ? grid : view === "list" ? list : (
        <>
          <div className="hidden md:block">{grid}</div>
          <div className="md:hidden">{list}</div>
        </>
      )}
      {footer}
    </div>
  );
}
