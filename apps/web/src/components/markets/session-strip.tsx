import { ChartFrame } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { classifySessions, completeness, sessionCounts } from "@/lib/markets/logic";
import type { ListingSeries, SessionStatus, VenueCalendar } from "@/lib/markets/types";
import { cn } from "@/lib/utils";

type StatusStyle = { label: string; glyph: string; cell: string; description: string };

/** Each status has its own glyph and fill pattern, so none depends on colour. */
const STATUS: Record<SessionStatus, StatusStyle> = {
  trading: { label: "Bar held", glyph: "", cell: "bg-accent-200", description: "Venue open and a bar is held." },
  half_day: { label: "Half day", glyph: "½", cell: "bg-accent-200", description: "Shortened session; a bar is held." },
  exchange_holiday: {
    label: "Exchange holiday",
    glyph: "H",
    cell: "bg-[repeating-linear-gradient(135deg,var(--color-border)_0_2px,transparent_2px_5px)] text-text-muted",
    description: "The exchange was closed for a national holiday. No bar expected.",
  },
  exchange_closure: {
    label: "Ad hoc closure",
    glyph: "C",
    cell: "bg-[repeating-linear-gradient(45deg,var(--color-border)_0_2px,transparent_2px_5px)] border border-dashed border-text-muted text-text-muted",
    description: "The exchange closed with no national holiday (halt, outage). No bar expected.",
  },
  national_holiday_open: {
    label: "National holiday, venue open",
    glyph: "N",
    cell: "bg-accent-200 border border-text-muted",
    description: "A national holiday fell on this date but the venue traded; a bar is held.",
  },
  missing_bar: {
    label: "Missing bar",
    glyph: "!",
    cell: "bg-surface border-2 border-loss text-loss",
    description: "The venue was open and no bar is held: a genuine gap.",
  },
};

const ORDER: SessionStatus[] = ["trading", "half_day", "national_holiday_open", "exchange_holiday", "exchange_closure", "missing_bar"];

/**
 * A week-by-week strip of the venue's sessions for one listing: genuine missing bars stand out
 * (heavy outline and "!"), while holidays and closures are patterned and explain themselves.
 * A national holiday never excuses a missing bar on a day the venue was open.
 */
export function SessionStrip({ listing, calendar }: { listing: ListingSeries; calendar: VenueCalendar }) {
  const cells = classifySessions(calendar, listing.raw.map((b) => b.date));
  const counts = sessionCounts(cells);
  const share = completeness(cells);
  const missing = cells.filter((c) => c.status === "missing_bar");

  // Group weekdays into calendar weeks (Monday start) for the strip.
  const weeks: (typeof cells)[] = [];
  for (const c of cells) {
    const dow = new Date(`${c.date}T00:00:00Z`).getUTCDay();
    if (weeks.length === 0 || dow === 1) weeks.push([]);
    weeks[weeks.length - 1].push(c);
  }
  const from = cells[0]?.date;
  const to = cells[cells.length - 1]?.date;

  return (
    <ChartFrame
      id="session-coverage"
      kicker="Venue sessions"
      title={`${calendar.venue}: which days should have a bar`}
      takeaway={
        missing.length > 0
          ? `${missing.length} open day${missing.length === 1 ? "" : "s"} without a bar (${missing.map((m) => formatDay(m.date)).join(", ")}); ${counts.exchange_holiday + counts.exchange_closure} closed day${counts.exchange_holiday + counts.exchange_closure === 1 ? "" : "s"} are explained by the venue calendar.`
          : "Every open session in the window has a bar; closed days are explained by the venue calendar."
      }
      units={`Weekdays ${from ? formatDay(from) : ""} to ${to ? formatDay(to) : ""}; completeness ${share === null ? "n/a" : formatPct(share, 1)} of open sessions`}
      provenance={{
        source: `Exchange calendar ${calendar.mic} (confirmed sessions) · national holidays from Nager.Date as suggestions only`,
        asOf: to ?? null,
        method: "Closed and half days come from confirmed exchange notices; a national holiday alone never marks a day closed",
        state: missing.length > 0 ? "partial" : "verified",
        reason: missing.length > 0 ? `${missing.length} open session${missing.length === 1 ? "" : "s"} without a bar.` : null,
        coverage: share === null ? null : { covered: counts.trading + counts.half_day + counts.national_holiday_open, total: counts.trading + counts.half_day + counts.national_holiday_open + counts.missing_bar, unit: "open sessions" },
      }}
      table={{
        caption: `${calendar.venue} sessions for ${listing.ticker}`,
        columns: ["Date", "Status", "Note"],
        rows: cells.filter((c) => c.status !== "trading").map((c) => [formatDay(c.date), STATUS[c.status].label, c.note ?? "—"]),
      }}
    >
      <div className="overflow-x-auto">
        <ol className="m-0 flex list-none gap-1.5 p-0" aria-label={`${calendar.venue} weekly session strip`}>
          {weeks.map((w) => (
            <li key={w[0].date} className="flex flex-col gap-0.5">
              <span className="num text-center text-[10px] text-text-faint">{formatDay(w[0].date).slice(0, 6)}</span>
              <ul className="m-0 flex list-none gap-0.5 p-0">
                {w.map((c) => {
                  const s = STATUS[c.status];
                  return (
                    <li
                      key={c.date}
                      data-status={c.status}
                      title={`${formatDay(c.date)} · ${s.label}${c.note ? ` · ${c.note}` : ""}`}
                      className={cn("flex size-5 items-center justify-center font-mono text-[10px] font-semibold", s.cell)}
                    >
                      <span aria-hidden="true">{s.glyph}</span>
                      <span className="sr-only">{`${formatDay(c.date)}: ${s.label}`}</span>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ol>
      </div>
      <ul className="m-0 grid list-none grid-cols-1 gap-x-6 gap-y-1.5 p-0 text-xs sm:grid-cols-2 xl:grid-cols-3">
        {ORDER.map((k) => (
          <li key={k} className="flex items-start gap-2">
            <span aria-hidden="true" className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center font-mono text-[10px] font-semibold", STATUS[k].cell)}>
              {STATUS[k].glyph}
            </span>
            <span>
              <span className="font-medium">{STATUS[k].label}</span> <span className="num text-text-muted">{counts[k]}</span>
              <span className="block text-text-muted">{STATUS[k].description}</span>
            </span>
          </li>
        ))}
      </ul>
    </ChartFrame>
  );
}
