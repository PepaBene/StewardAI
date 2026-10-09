import { FAMILY_ORDER, formFamily, type Filing, type FormFamily } from "./model";

/*
 * Geometry for the known-at rail. Time runs left to right. Each filing is one mark placed at
 * `known_at` (the moment it counts as known); when the filing date or acceptance time is earlier,
 * a thin tail runs back to it, so "filed on the 20th, known from the 20th 23:59" and "accepted
 * 14:00, known 14:00" read differently. An amendment draws a link back to the version it amends.
 * The domain covers every filing (even ones after the cutoff) so scrubbing the cutoff moves a
 * line across a stable picture instead of re-scaling it.
 */

export type RailMark = {
  id: number;
  filing: Filing;
  lane: number;
  family: FormFamily;
  x: number;
  /** Where the tail starts (earliest of filed/accepted/known), or null when it equals `x`. */
  tailX: number | null;
  /** True when the filing is known at the cutoff. */
  known: boolean;
};

export type RailLink = { from: number; to: number; fromX: number; toX: number; fromLane: number; toLane: number };
export type RailTick = { x: number; label: string; major: boolean };

export type Rail = {
  width: number;
  height: number;
  laneHeight: number;
  lanes: { family: FormFamily; y: number }[];
  marks: RailMark[];
  links: RailLink[];
  ticks: RailTick[];
  cutoffX: number;
  domain: [number, number];
  plot: { left: number; right: number; top: number; bottom: number };
};

const t = (iso: string | null | undefined) => (iso ? Date.parse(iso) : Number.NaN);
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_MS = 86_400_000;

export function timeTicks(t0: number, t1: number): { at: number; label: string; major: boolean }[] {
  const out: { at: number; label: string; major: boolean }[] = [];
  const start = new Date(t0);
  const spanDays = (t1 - t0) / DAY_MS;
  const stepMonths = spanDays > 900 ? 6 : spanDays > 400 ? 3 : spanDays > 150 ? 2 : 1;
  let y = start.getUTCFullYear();
  let m = start.getUTCMonth() - (start.getUTCMonth() % stepMonths);
  for (let guard = 0; guard < 240; guard++) {
    const at = Date.UTC(y, m, 1);
    if (at > t1) break;
    if (at >= t0) out.push({ at, label: m === 0 ? String(y) : MONTH[m], major: m === 0 });
    m += stepMonths;
    if (m > 11) {
      y += Math.floor(m / 12);
      m %= 12;
    }
  }
  return out;
}

export function layoutRail(
  filings: readonly Filing[],
  cutoffMs: number,
  {
    width = 960,
    laneHeight = 34,
    left = 132,
    right = 24,
    top = 8,
    axis = 26,
    until,
  }: { width?: number; laneHeight?: number; left?: number; right?: number; top?: number; axis?: number; until?: number } = {},
): Rail {
  const present = FAMILY_ORDER.filter((fam) => filings.some((f) => formFamily(f.form) === fam));
  const lanes = (present.length ? present : (["annual"] as FormFamily[])).map((family, i) => ({ family, y: top + i * laneHeight + laneHeight / 2 }));
  const height = top + lanes.length * laneHeight + axis;

  const stamps = filings.flatMap((f) => [t(f.known_at), t(f.filed_at), t(f.accepted_at)]).filter(Number.isFinite);
  // The domain depends on the filings (and a fixed `until`, normally the page-load time), never on
  // the cutoff, so dragging the cutoff moves a line across a picture that does not re-scale.
  const end = until ?? (stamps.length ? Math.max(...stamps) : Date.now());
  const lo = Math.min(...(stamps.length ? stamps : [end]));
  const hi = Math.max(...(stamps.length ? stamps : [end]), end);
  const pad = Math.max((hi - lo) * 0.04, 3 * DAY_MS);
  const domain: [number, number] = [lo - pad, hi + pad];
  const plotW = width - left - right;
  const x = (time: number) => left + ((time - domain[0]) / (domain[1] - domain[0])) * plotW;

  const laneOf = new Map(lanes.map((l, i) => [l.family, i]));
  const marks: RailMark[] = filings.map((filing) => {
    const family = formFamily(filing.form);
    const known = t(filing.known_at);
    const earliest = Math.min(...[known, t(filing.filed_at), t(filing.accepted_at)].filter(Number.isFinite));
    const markX = x(known);
    const tail = x(earliest);
    return {
      id: filing.version_id,
      filing,
      lane: laneOf.get(family) ?? 0,
      family,
      x: markX,
      tailX: markX - tail > 1.5 ? tail : null,
      known: known <= cutoffMs,
    };
  });
  const byId = new Map(marks.map((m) => [m.id, m]));
  const links: RailLink[] = [];
  for (const m of marks) {
    const target = m.filing.amends_version_id != null ? byId.get(m.filing.amends_version_id) : undefined;
    if (target) links.push({ from: m.id, to: target.id, fromX: m.x, toX: target.x, fromLane: m.lane, toLane: target.lane });
  }
  const ticks = timeTicks(domain[0], domain[1]).map((k) => ({ x: x(k.at), label: k.label, major: k.major }));
  return {
    width,
    height,
    laneHeight,
    lanes,
    marks,
    links,
    ticks,
    cutoffX: x(Math.min(domain[1], Math.max(domain[0], cutoffMs))),
    domain,
    plot: { left, right: width - right, top, bottom: top + lanes.length * laneHeight },
  };
}

/** The cutoff a pointer or key press at `x` means (inverse of the layout's time scale). */
export function cutoffFromX(rail: Rail, x: number): number {
  const { left, right } = rail.plot;
  const ratio = Math.min(1, Math.max(0, (x - left) / (right - left)));
  return rail.domain[0] + ratio * (rail.domain[1] - rail.domain[0]);
}
