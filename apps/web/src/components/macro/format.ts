import { formatNumber, formatPct } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { formatDate } from "@/lib/format";
import type { NextRelease } from "@/lib/macro/types";

/** A figure with digits that scale to its size, so 0.043 and 4,310 both keep their meaning. */
export function formatFigure(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return "n/a";
  const a = Math.abs(value);
  const digits = a === 0 ? 0 : a < 0.1 ? 4 : a < 10 ? 2 : a < 1000 ? 1 : 0;
  return formatNumber(value, digits);
}

/** Signed variant for changes: +0.3, −1.2; zero stays "0". */
export function formatChange(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return "n/a";
  const body = formatFigure(Math.abs(value));
  return value > 0 ? `+${body}` : value < 0 ? `−${body}` : body;
}

/** "27 Sep 2026" from a date or date-time string; the original text when it does not parse. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const d = iso.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? formatDate(d) : iso;
}

/**
 * A share of the portfolio (fraction) with digits that scale to its size, so a legend edge of
 * 0.025% does not round to "0.03%" and a real 0.004% does not print as "0.00%". Positive but
 * below 0.0001% reads "<0.0001%"; zero stays "0.0%".
 */
export function formatShare(v: number): string {
  if (!Number.isFinite(v)) return "n/a";
  const a = Math.abs(v);
  if (a === 0) return formatPct(0, 1);
  if (a < 1e-6) return `${v < 0 ? "−" : ""}<0.0001%`;
  const digits = a >= 1e-3 ? 1 : a >= 1e-4 ? 3 : 4;
  return formatPct(v, digits);
}

export type NextReleaseView = {
  /** Short label of what the date is: "published date", "published rule", "estimate" or "unknown". */
  label: string;
  /** The date (and time, with its zone) text, e.g. "Sep 27, 2026, 14:15 (Europe/Prague, assumed from the bank's location)" or "about Sep 27, 2026". */
  text: string;
  /** Visible qualifier of how the date was obtained; null for a date the provider lists itself. */
  qualifier: string | null;
  /** Plain-language sentence for screen readers and the table view; never calls an assumption published. */
  sentence: string;
  /** IANA zone shown with the time, or null. */
  zone: string | null;
  /** Table text for where the zone comes from. */
  zoneSourceLabel: string;
  /** The provider calendar link, when there is one. */
  source: { provider: string; url: string; title: string | null } | null;
};

/** "14:15:00" -> "14:15"; the provider's time exactly as published, never converted. */
function hhmm(time: string): string {
  const m = /^(\d{2}:\d{2})(:\d{2}(\.\d+)?)?$/.exec(time);
  return m ? m[1] : time;
}

export const RULE_QUALIFIER = "per the provider's published schedule rule";
export const ESTIMATE_QUALIFIER = "estimate, based on observed cadence";
const ASSUMED = "assumed from the bank's location";

/**
 * How a series' next release reads. A provider-calendar date is shown as published (a date, not
 * a promise); a date from a rule the provider publishes says so; an estimate is labelled as based
 * on observed cadence; neither is unknown. A stated zone is shown plainly, an assumed one with
 * the assumption spelled out. Times are shown as published, never converted.
 */
export function nextReleaseView(n: NextRelease): NextReleaseView {
  if (n.kind === "calendar" || n.kind === "rule") {
    const date = day(n.date);
    let text = date;
    let timeNote = "";
    let zoneSourceLabel = "n/a (no time given)";
    if (n.time) {
      const t = hhmm(n.time);
      if (n.timezone && n.timezoneSource === "stated_by_provider") {
        text = `${date}, ${t} (${n.timezone})`;
        timeNote = `, time zone ${n.timezone} as stated by the provider`;
        zoneSourceLabel = "Stated by provider";
      } else if (n.timezone && n.timezoneSource === "agency_location") {
        text = `${date}, ${t} (${n.timezone}, ${ASSUMED})`;
        timeNote = `, time zone ${n.timezone} ${ASSUMED}, not stated by the provider`;
        zoneSourceLabel = "Assumed from the bank's location";
      } else {
        text = `${date}, ${t} (time zone not given)`;
        timeNote = ", time zone not given";
        zoneSourceLabel = "Not given";
      }
    }
    const who = n.source ? `${n.source.provider} release calendar` : "the provider's release calendar";
    if (n.kind === "rule") {
      return {
        label: "published rule",
        text,
        qualifier: RULE_QUALIFIER,
        sentence: `Next release ${text.replace(/ \(.*\)$/, "")}${timeNote}, ${RULE_QUALIFIER} (${who}), not a date listed individually; the provider may still postpone it.`,
        zone: n.timezone,
        zoneSourceLabel,
        source: n.source,
      };
    }
    return {
      label: "published date",
      text,
      qualifier: null,
      sentence: `Next release ${text.replace(/ \(.*\)$/, "")}${timeNote}, as published in ${who}; the provider may still reschedule it.`,
      zone: n.timezone,
      zoneSourceLabel,
      source: n.source,
    };
  }
  if (n.kind === "estimate") {
    const text = `about ${day(n.date)}`;
    return {
      label: "estimate",
      text,
      qualifier: ESTIMATE_QUALIFIER,
      sentence: `Next release ${text}: an estimate based on observed cadence, not a published date.`,
      zone: null,
      zoneSourceLabel: "n/a",
      source: null,
    };
  }
  return { label: "unknown", text: "unknown", qualifier: null, sentence: "Next release unknown: no provider calendar entry or published rule, and too little release history to estimate it.", zone: null, zoneSourceLabel: "n/a", source: null };
}
