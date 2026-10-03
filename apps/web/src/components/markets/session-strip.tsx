import { ChartFrame } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { sessionCompleteness, sessionCounts } from "@/lib/markets/logic";
import type { SessionCell, SessionStatus } from "@/lib/markets/types";
import type { DataState } from "@/lib/data-state/states";
import { cn } from "@/lib/utils";

type StatusStyle = { label: string; glyph: string; cell: string; description: string };

/** Each status has its own glyph and fill pattern, so none depends on colour. */
const STATUS: Record<SessionStatus, StatusStyle> = {
  trading: { label: "Bar held", glyph: "", cell: "bg-accent-200", description: "The exchange was expected open and a bar is held." },
  half_day: { label: "Half day", glyph: "½", cell: "bg-accent-200", description: "Shortened session per a confirmed exchange record; a bar is held." },
  exchange_holiday: {
    label: "Exchange holiday",
    glyph: "H",
    cell: "bg-[repeating-linear-gradient(135deg,var(--color-border)_0_2px,transparent_2px_5px)] text-text-muted",
    description: "A closure confirmed in the Jobs calendar. No bar expected.",
  },
  exchange_closure: {
    label: "Ad hoc closure",
    glyph: "C",
    cell: "bg-[repeating-linear-gradient(45deg,var(--color-border)_0_2px,transparent_2px_5px)] border border-dashed border-text-muted text-text-muted",
    description: "Not an expected session and not a confirmed holiday (closure notice, halt). No bar expected.",
  },
  national_holiday_open: {
    label: "National holiday, exchange open",
    glyph: "N",
    cell: "bg-accent-200 border border-text-muted",
    description: "A national holiday suggestion falls on this date but the exchange is expected open and a bar is held.",
  },
  missing_bar: { label: "Missing bar", glyph: "!", cell: "bg-surface border-2 border-loss text-loss", description: "The exchange was expected open and no bar is held: a genuine gap." },
  upcoming: { label: "Upcoming", glyph: "·", cell: "bg-surface border border-dashed border-border text-text-faint", description: "Not yet complete; not counted." },
  before_history: { label: "Before stored history", glyph: "–", cell: "bg-surface-muted text-text-faint", description: "Earlier than the first stored bar; not counted as missing." },
};

const ORDER: SessionStatus[] = ["trading", "half_day", "national_holiday_open", "exchange_holiday", "exchange_closure", "missing_bar", "upcoming", "before_history"];

const BASIS_TEXT: Record<string, string> = {
  confirmed_calendar: "every weekday has a confirmed exchange record",
  partial_confirmed_calendar: "some weekdays have confirmed exchange records, the rest are weekdays less configured closures",
  weekdays_minus_configured_closures: "no confirmed exchange records: weekdays less closures confirmed in the Jobs calendar",
};

/**
 * Weekly strip of the venue's sessions for one holding. A genuine missing bar stands out (heavy
 * outline and "!"); holidays and closures are patterned and name their source. A national holiday
 * never excuses a missing bar on a day the exchange is expected open.
 */
export function SessionStrip({
  ticker,
  mic,
  cells,
  basis,
  confirmedDays,
  suggestionsNote,
  asOf,
}: {
  ticker: string;
  mic: string;
  cells: readonly SessionCell[];
  basis: string;
  confirmedDays: number;
  /** Plain statement of what the national-holiday list could and could not cover. */
  suggestionsNote: string;
  asOf: string | null;
}) {
  const counts = sessionCounts(cells);
  const { held, expected, share } = sessionCompleteness(cells);
  const missing = cells.filter((c) => c.status === "missing_bar");
  const closed = counts.exchange_holiday + counts.exchange_closure;
  const weeks: SessionCell[][] = [];
  for (const c of cells) {
    const dow = new Date(`${c.date}T00:00:00Z`).getUTCDay();
    if (weeks.length === 0 || dow === 1) weeks.push([]);
    weeks[weeks.length - 1].push(c);
  }
  const state: DataState = expected === 0 ? "not_covered" : missing.length > 0 ? "partial" : basis === "weekdays_minus_configured_closures" ? "partial" : "verified";

  return (
    <ChartFrame
      id="session-coverage"
      kicker={`Venue sessions · ${mic}`}
      title={`${ticker}: which days should have a bar`}
      takeaway={
        expected === 0
          ? "No past expected session falls inside the stored history in this window."
          : missing.length > 0
            ? `${missing.length} expected session${missing.length === 1 ? "" : "s"} without a bar (${missing.slice(0, 6).map((m) => formatDay(m.date)).join(", ")}${missing.length > 6 ? ", …" : ""}); ${closed} closed day${closed === 1 ? "" : "s"} explained by the exchange calendar.`
            : `Every expected session in the window has a bar; ${closed} closed day${closed === 1 ? "" : "s"} explained by the exchange calendar.`
      }
      units={`Weekdays ${cells[0] ? formatDay(cells[0].date) : ""} to ${cells.length ? formatDay(cells[cells.length - 1].date) : ""} · ${held} of ${expected} expected sessions held${share === null ? "" : ` (${formatPct(share, 1)})`}`}
      hasValue={cells.length > 0}
      provenance={{
        source: `Exchange calendar ${mic} (GET /exchange-sessions/${mic} and /expected) · closures from /calendar/exchanges`,
        asOf,
        method: `Expected sessions: ${BASIS_TEXT[basis] ?? basis} (${confirmedDays} confirmed day${confirmedDays === 1 ? "" : "s"}). ${suggestionsNote}`,
        state,
        reason:
          basis === "weekdays_minus_configured_closures"
            ? "No confirmed exchange records cover this window, so an unconfirmed holiday would show here as a missing bar."
            : missing.length > 0
              ? `${missing.length} expected session${missing.length === 1 ? "" : "s"} without a bar.`
              : null,
        coverage: expected > 0 ? { covered: held, total: expected, unit: "expected sessions" } : null,
      }}
      table={{
        caption: `${mic} sessions for ${ticker}, days that are not plain trading days`,
        columns: ["Date", "Status", "Note"],
        rows: cells.filter((c) => c.status !== "trading").map((c) => [formatDay(c.date), STATUS[c.status].label, c.note ?? "n/a"]),
      }}
    >
      <div className="relative overflow-x-auto">
        <ol className="m-0 flex list-none gap-1.5 p-0" aria-label={`${mic} weekly session strip`}>
          {weeks.map((w) => (
            <li key={w[0].date} className="flex flex-col gap-0.5">
              <span className="num text-center text-[10px] text-text-faint">{formatDay(w[0].date).slice(0, 6)}</span>
              <ul className="m-0 flex list-none gap-0.5 p-0">
                {w.map((c) => {
                  const s = STATUS[c.status];
                  return (
                    <li key={c.date} data-status={c.status} title={`${formatDay(c.date)} · ${s.label}${c.note ? ` · ${c.note}` : ""}`} className={cn("flex size-5 items-center justify-center font-mono text-[10px] font-semibold", s.cell)}>
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
          <li key={k} className="flex min-w-0 items-start gap-2">
            <span aria-hidden="true" className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center font-mono text-[10px] font-semibold", STATUS[k].cell)}>
              {STATUS[k].glyph}
            </span>
            <span className="min-w-0">
              <span className="font-medium">{STATUS[k].label}</span> <span className="num text-text-muted">{counts[k]}</span>
              <span className="block text-text-muted">{STATUS[k].description}</span>
            </span>
          </li>
        ))}
      </ul>
    </ChartFrame>
  );
}
