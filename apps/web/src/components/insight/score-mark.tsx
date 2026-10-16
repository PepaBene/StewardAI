"use client";

import type { KeyboardEvent } from "react";

import { AXIS_LABEL, MARK_AXES, type MarkAxis, type MarkPetal } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

import { closedSplinePath, markPoints, meanKnown, scoreRadius, scoreTone, type MarkTone } from "./score-mark-geometry";

/*
 * F18 ScoreMark (visual spec B4): the one glyph for a five-axis profile. The five scores (0-6) are
 * joined by a smoothed closed curve and filled with the tone of their mean (weak / mixed / strong)
 * at 70%, stroked 1.5 px at full strength. A zero score keeps a small core, so 0 is never read as
 * missing; an unknown axis is a dashed notch from the core to the ring with "?" (a not-applicable
 * axis says "n/a"). Sizes: "row" 24 px, "header" 64 px, "sidebar" 120 px (rings, axis initials) and
 * "card" 240 px (rings, full axis names, clickable axis points).
 */

export type ScoreMarkSize = "row" | "header" | "sidebar" | "card";

const SIZE: Record<ScoreMarkSize, number> = { row: 24, header: 64, sidebar: 120, card: 240 };
/** ViewBox side and ring radius per size, in viewBox units (1 unit = 1 px except the card). */
const BOX: Record<ScoreMarkSize, { view: number; R: number }> = {
  row: { view: 24, R: 11 },
  header: { view: 64, R: 30 },
  sidebar: { view: 120, R: 44 },
  card: { view: 300, R: 92 },
};

const FILL: Record<MarkTone, string> = {
  weak: "fill-score-mark-weak",
  mixed: "fill-score-mark-mixed",
  strong: "fill-score-mark-strong",
};
const STROKE: Record<MarkTone, string> = {
  weak: "stroke-score-mark-weak",
  mixed: "stroke-score-mark-mixed",
  strong: "stroke-score-mark-strong",
};

const INITIAL: Record<MarkAxis, string> = { value: "V", future: "F", past: "P", health: "H", dividend: "D" };

function describe(petals: readonly MarkPetal[], nameOf: (a: MarkAxis) => string): string {
  return petals
    .map((p) =>
      p.notApplicable
        ? `${nameOf(p.axis)} not applicable`
        : p.fraction === null
          ? `${nameOf(p.axis)} unknown`
          : `${nameOf(p.axis)} ${Math.round(p.fraction * 6 * 10) / 10} of 6`,
    )
    .join(", ");
}

export function ScoreMark({
  petals,
  size = "row",
  selected,
  onSelect,
  label,
  missing,
  axisNames,
  className,
}: {
  petals: readonly MarkPetal[];
  size?: ScoreMarkSize;
  /** Highlighted axis in the card form. */
  selected?: MarkAxis | null;
  /** Makes the axis points buttons (card form only). */
  onSelect?: (axis: MarkAxis) => void;
  /** Accessible name prefix, e.g. the company name. */
  label?: string;
  /** Names of the missing inputs per axis, shown in the notch tooltip. */
  missing?: Partial<Record<MarkAxis, string>>;
  /** Names for the five slots when they are not the company check axes (e.g. a fund's questions). */
  axisNames?: Partial<Record<MarkAxis, string>>;
  className?: string;
}) {
  const nameOf = (a: MarkAxis) => axisNames?.[a] ?? AXIS_LABEL[a];
  const initialOf = (a: MarkAxis) => (axisNames?.[a] ? axisNames[a]!.charAt(0).toUpperCase() : INITIAL[a]);
  const px = SIZE[size];
  const { view: V, R } = BOX[size];
  const c = V / 2;
  const card = size === "card";
  const showRings = size === "sidebar" || card;
  const scores = petals.map((p) => (p.notApplicable || p.fraction === null ? null : p.fraction * 6));
  const pts = markPoints(scores, R, c, c);
  const tone = scoreTone(meanKnown(scores));
  const summary = `${label ? `${label}: ` : ""}${describe(petals, nameOf)}`;
  const unit = V / px;
  const labelAt = R + (card ? 22 : 11);

  function onKeyDown(e: KeyboardEvent<HTMLSpanElement>) {
    const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!dir || !onSelect) return;
    e.preventDefault();
    const i = Math.max(0, MARK_AXES.findIndex((a) => a === selected));
    const next = MARK_AXES[(i + dir + MARK_AXES.length) % MARK_AXES.length];
    onSelect(next);
    const buttons = e.currentTarget.querySelectorAll("button");
    (buttons[MARK_AXES.indexOf(next)] as HTMLButtonElement | undefined)?.focus();
  }

  return (
    <span
      className={cn("relative inline-block shrink-0 align-middle", card && "w-full max-w-[200px] sm:max-w-[240px]", className)}
      style={card ? undefined : { width: px, height: px }}
      onKeyDown={card && onSelect ? onKeyDown : undefined}
    >
      <svg viewBox={`0 0 ${V} ${V}`} className="block h-auto w-full overflow-visible" role="img" aria-label={summary}>
        <title>{summary}</title>
        {(showRings ? [2, 4, 6] : [6]).map((s) => (
          <circle key={s} cx={c} cy={c} r={scoreRadius(s, R)} fill="none" className="stroke-chart-track" strokeWidth={unit} />
        ))}
        {tone ? (
          <path
            d={closedSplinePath(pts)}
            fillOpacity={0.7}
            strokeWidth={1.5 * unit}
            strokeLinejoin="round"
            className={cn(FILL[tone], STROKE[tone])}
            opacity={selected ? 0.95 : 1}
          />
        ) : null}
        {pts.map((p, i) => {
          const petal = petals[i];
          if (!petal || (p.score !== null && !petal.notApplicable)) return null;
          const na = petal.notApplicable;
          const reason = missing?.[petal.axis];
          const lx = c + (R + 8 * (card ? 1.5 : 1)) * Math.cos(p.angle);
          const ly = c + (R + 8 * (card ? 1.5 : 1)) * Math.sin(p.angle);
          return (
            <g key={petal.axis}>
              <title>{`${nameOf(petal.axis)}: ${na ? "not applicable" : "unknown"}${reason ? `, missing ${reason}` : ""}`}</title>
              <line x1={p.coreX} y1={p.coreY} x2={p.ringX} y2={p.ringY} className="stroke-text-faint" strokeWidth={2 * unit} strokeDasharray={`${3 * unit} ${3 * unit}`} />
              {showRings ? (
                <text x={lx} y={ly} textAnchor="middle" dominantBaseline="central" fontSize={(card ? 12 : 10) * unit} className="fill-text-faint">
                  {na ? "n/a" : "?"}
                </text>
              ) : null}
            </g>
          );
        })}
        {showRings
          ? petals.map((p, i) => {
              const a = pts[i].angle;
              const lx = c + labelAt * Math.cos(a);
              const ly = c + labelAt * Math.sin(a) + (Math.abs(Math.cos(a)) < 0.25 ? (Math.sin(a) < 0 ? -2 : 8) : 4);
              const anchor = Math.abs(Math.cos(a)) < 0.25 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
              return (
                <text
                  key={p.axis}
                  x={lx}
                  y={ly}
                  textAnchor={card ? anchor : "middle"}
                  fontSize={(card ? 12.5 : 11) * unit}
                  className={cn(selected === p.axis ? "fill-text font-semibold" : "fill-text-muted")}
                  aria-hidden="true"
                >
                  {card ? nameOf(p.axis) : initialOf(p.axis)}
                </text>
              );
            })
          : null}
        {card
          ? pts.map((p, i) => (
              <circle
                key={petals[i].axis}
                cx={p.x}
                cy={p.y}
                r={6 * unit}
                strokeWidth={2 * unit}
                className={cn("stroke-surface", selected === petals[i].axis ? "fill-accent" : tone ? FILL[tone] : "fill-chart-unknown")}
              />
            ))
          : null}
      </svg>
      {card && onSelect ? (
        <span className="absolute inset-0" role="group" aria-label="Axes">
          {pts.map((p, i) => (
            <button
              key={petals[i].axis}
              type="button"
              aria-pressed={selected === petals[i].axis}
              aria-label={`Show ${nameOf(petals[i].axis)}`}
              onClick={() => onSelect(petals[i].axis)}
              className="absolute size-8 -translate-x-1/2 -translate-y-1/2 rounded-full focus-visible:outline-2 focus-visible:outline-accent"
              style={{ left: `${(p.x / V) * 100}%`, top: `${(p.y / V) * 100}%` }}
            />
          ))}
        </span>
      ) : null}
    </span>
  );
}

/** Legend for the mark's non-score states, shown once near large marks. */
export function ScoreMarkKey({ className }: { className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted", className)}>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-2.5 w-4 bg-score-mark-strong opacity-70" />
        Further out, more checks pass
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-0 w-4 border-t-2 border-dashed border-text-faint" />
        ? Unknown: inputs missing
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-0 w-4 border-t-2 border-dashed border-text-faint" />
        n/a Not applicable
      </li>
    </ul>
  );
}
