/*
 * A plain-English reading of a 5-field cron expression (minute hour day month weekday),
 * shown next to each schedule in the jobs editor. It covers the shapes a schedule usually
 * takes and says "custom" for anything else; the backend validates the expression itself.
 */

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const DAY_ABBR = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export type CronRun = {
  /** Days after the start day (0 = the start day). */
  day: number;
  /** Minutes after midnight, 0..1439. */
  minute: number;
};

type FieldSpec = { min: number; max: number; names?: readonly string[]; nameBase?: number };

const FIELD_SPECS: readonly FieldSpec[] = [
  { min: 0, max: 59 },
  { min: 0, max: 23 },
  { min: 1, max: 31 },
  { min: 1, max: 12, names: MONTHS.slice(1).map((m) => m.toLowerCase()), nameBase: 1 },
  { min: 0, max: 7, names: DAY_ABBR, nameBase: 0 },
];

function fieldValue(token: string, spec: FieldSpec): number | null {
  if (isInt(token)) return Number(token);
  const i = spec.names?.indexOf(token.toLowerCase().slice(0, 3)) ?? -1;
  return i >= 0 ? i + (spec.nameBase ?? 0) : null;
}

/** The values one field matches, or null when it is malformed. Day-of-week 7 counts as Sunday. */
function expandField(field: string, spec: FieldSpec): Set<number> | null {
  const out = new Set<number>();
  for (const part of field.split(",")) {
    const [range, stepText, ...extra] = part.split("/");
    if (extra.length || range === "") return null;
    const step = stepText === undefined ? 1 : Number(stepText);
    if (!Number.isInteger(step) || step < 1) return null;
    let lo: number | null;
    let hi: number | null;
    if (range === "*") {
      lo = spec.min;
      hi = spec.max;
    } else if (range.includes("-")) {
      const [a, b, ...rest] = range.split("-");
      if (rest.length) return null;
      lo = fieldValue(a, spec);
      hi = fieldValue(b, spec);
    } else {
      lo = fieldValue(range, spec);
      // "5/10" means from 5 to the end in steps of 10.
      hi = stepText === undefined ? lo : spec.max;
    }
    if (lo === null || hi === null || lo < spec.min || hi > spec.max || lo > hi) return null;
    for (let v = lo; v <= hi; v += step) out.add(spec.max === 7 && v === 7 ? 0 : v);
  }
  return out;
}

/**
 * Every run of a standard 5-field cron expression (minute hour day-of-month month day-of-week)
 * over `days` days from `from`, read as wall-clock time: `from` carries the schedule's local
 * date and time in its UTC fields (build it with `Date.UTC`), so daylight-saving shifts never
 * move a run. Supports `*`, lists, ranges and steps, and month and weekday names. As in cron, a
 * run matches day-of-month OR day-of-week when both are restricted. Returns null when the
 * expression is not valid. Runs before `from` on the first day are left out.
 */
export function expandCron(expression: string, from: Date, days = 7): CronRun[] | null {
  const fields = expression.trim().split(/\s+/).filter(Boolean);
  if (fields.length !== 5) return null;
  const sets = fields.map((f, i) => expandField(f, FIELD_SPECS[i]));
  if (sets.some((s) => s === null)) return null;
  const [minutes, hours, doms, months, dows] = sets as Set<number>[];
  const domAny = fields[2].startsWith("*");
  const dowAny = fields[4].startsWith("*");
  const startMinute = from.getUTCHours() * 60 + from.getUTCMinutes();
  const runs: CronRun[] = [];
  for (let day = 0; day < days; day++) {
    const d = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate() + day));
    if (!months.has(d.getUTCMonth() + 1)) continue;
    const domOk = doms.has(d.getUTCDate());
    const dowOk = dows.has(d.getUTCDay());
    const dayOk = domAny || dowAny ? domOk && dowOk : domOk || dowOk;
    if (!dayOk) continue;
    for (const h of [...hours].sort((a, b) => a - b)) {
      for (const m of [...minutes].sort((a, b) => a - b)) {
        const minute = h * 60 + m;
        if (day === 0 && minute < startMinute) continue;
        runs.push({ day, minute });
      }
    }
  }
  return runs;
}

/** The schedule's current local date and time as a wall-clock `Date` (see `expandCron`). */
export function wallClockNow(timeZone: string, now: Date = new Date()): Date {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
    }).formatToParts(now);
    const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
    return new Date(Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute")));
  } catch {
    return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes()));
  }
}

export type CronReading = {
  ok: boolean;
  text: string;
};

const isInt = (s: string) => /^\d+$/.test(s);
const pad = (n: number) => String(n).padStart(2, "0");

/** Weekday token (number in standard cron, 0 or 7 = Sunday, or a name) -> 0..6, Sunday = 0. */
function weekday(token: string): number | null {
  if (isInt(token)) {
    const n = Number(token);
    return n >= 0 && n <= 7 ? n % 7 : null;
  }
  const i = DAY_ABBR.indexOf(token.toLowerCase().slice(0, 3));
  return i >= 0 ? i : null;
}

/** null: any day; undefined: a shape this reader does not cover. */
function describeWeekdays(field: string): string | null | undefined {
  if (field === "*") return null;
  const parts = field.split(",");
  const days: number[] = [];
  for (const part of parts) {
    const range = part.split("-");
    if (range.length === 2) {
      const a = weekday(range[0]);
      const b = weekday(range[1]);
      if (a === null || b === null) return undefined;
      // A range ending at 7 (Sunday) or wrapping past Saturday.
      const end = range[1] === "7" ? 7 : b;
      for (let d = a; d <= (end < a ? end + 7 : end); d++) days.push(d % 7);
    } else {
      const d = weekday(part);
      if (d === null) return undefined;
      days.push(d);
    }
  }
  const unique = [...new Set(days)].sort((x, y) => x - y);
  if (unique.join() === "1,2,3,4,5") return "on weekdays (Mon–Fri)";
  if (unique.join() === "0,6") return "on weekends";
  if (unique.length === 7) return null;
  if (unique.length === 1) return `on ${DAY_NAMES[unique[0]]}s`;
  return `on ${unique.map((d) => DAY_NAMES[d].slice(0, 3)).join(", ")}`;
}

function describeTime(minute: string, hour: string): string | null {
  if (isInt(minute) && isInt(hour)) return `at ${pad(Number(hour))}:${pad(Number(minute))}`;
  if (minute === "*" && hour === "*") return "every minute";
  const everyMinutes = /^\*\/(\d+)$/.exec(minute);
  if (everyMinutes && hour === "*") return `every ${everyMinutes[1]} minutes`;
  if (!isInt(minute)) return null;
  const m = pad(Number(minute));
  if (hour === "*") return `every hour at :${m}`;
  const step = /^(?:(\*)|(\d+)-(\d+))\/(\d+)$/.exec(hour);
  if (step) {
    const every = `every ${step[4]} h`;
    return step[1] ? `${every} at :${m}` : `${every} from ${pad(Number(step[2]))}:${m} to ${pad(Number(step[3]))}:${m}`;
  }
  const range = /^(\d+)-(\d+)$/.exec(hour);
  if (range) return `every hour at :${m} from ${pad(Number(range[1]))}:${m} to ${pad(Number(range[2]))}:${m}`;
  if (/^\d+(,\d+)+$/.test(hour)) return `at ${hour.split(",").map((h) => `${pad(Number(h))}:${m}`).join(", ")}`;
  return null;
}

export function describeCron(expression: string): CronReading {
  const fields = expression.trim().split(/\s+/).filter(Boolean);
  if (fields.length !== 5) {
    return {
      ok: false,
      text: `A cron expression has 5 fields (minute hour day month weekday); this has ${fields.length}.`,
    };
  }
  const [minute, hour, day, month, dow] = fields;
  const time = describeTime(minute, hour);
  const weekdays = describeWeekdays(dow);
  const months = /^\d+(,\d+)*$/.test(month) ? month.split(",").map((m) => MONTHS[Number(m)]) : null;
  if (
    time === null ||
    weekdays === undefined ||
    !(day === "*" || isInt(day)) ||
    !(month === "*" || (months && months.every(Boolean)))
  ) {
    return { ok: true, text: "Custom schedule." };
  }
  const parts = [time];
  if (isInt(day)) parts.push(`on day ${day} of the month`);
  if (months) parts.push(`in ${months.join(", ")}`);
  if (weekdays) parts.push(weekdays);
  else if (!isInt(day) && !months && time.startsWith("at ")) parts.push("every day");
  const text = parts.join(" ");
  return { ok: true, text: `${text.charAt(0).toUpperCase()}${text.slice(1)}.` };
}
