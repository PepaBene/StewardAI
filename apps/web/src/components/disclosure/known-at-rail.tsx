"use client";

import { useMemo, useRef } from "react";

import { dayOf, endOfDayMs, FAMILY_LABEL, formatDay, formatStamp, type Filing } from "@/lib/disclosure/model";
import { cutoffFromX, layoutRail } from "@/lib/disclosure/rail";
import { cn } from "@/lib/utils";

const DAY_MS = 86_400_000;
const WIDTH = 960;

/**
 * The known-at rail: one lane per kind of document, one mark per filing at the moment it counts as
 * known. A vertical cutoff line splits what the owner could have seen from what came later; marks
 * after it are hollow. The line drags, moves with the arrow keys, and follows the date field, so a
 * pointer is never required. The filings table below carries the same facts as text.
 */
export function KnownAtRail({
  filings,
  cutoffMs,
  until,
  selectedId,
  onCutoff,
  onSelect,
}: {
  filings: readonly Filing[];
  cutoffMs: number;
  /** Fixed right edge of the picture (the page-load time), so the scale never moves while scrubbing. */
  until: number;
  selectedId: number | null;
  onCutoff: (ms: number) => void;
  onSelect: (versionId: number) => void;
}) {
  const rail = useMemo(() => layoutRail(filings, cutoffMs, { width: WIDTH, until }), [filings, cutoffMs, until]);
  const svg = useRef<SVGSVGElement>(null);

  function fromPointer(e: React.PointerEvent) {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0) return;
    onCutoff(cutoffFromX(rail, ((e.clientX - box.left) / box.width) * WIDTH));
  }

  function onKey(e: React.KeyboardEvent) {
    const step = e.shiftKey ? 30 * DAY_MS : DAY_MS;
    const moves: Record<string, number> = { ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step };
    if (e.key in moves) {
      e.preventDefault();
      onCutoff(Math.min(rail.domain[1], Math.max(rail.domain[0], cutoffMs + moves[e.key])));
    } else if (e.key === "Home") {
      e.preventDefault();
      onCutoff(rail.domain[0]);
    } else if (e.key === "End") {
      e.preventDefault();
      onCutoff(rail.domain[1]);
    }
  }

  const known = rail.marks.filter((m) => m.known).length;
  const laneY = (lane: number) => rail.lanes[lane]?.y ?? rail.lanes[0].y;

  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap items-end justify-between gap-3">
        <span className="text-sm text-text-muted">
          Known by <strong className="num font-semibold text-text">{formatDay(new Date(cutoffMs).toISOString())}</strong>: {known} of {rail.marks.length} filings.
          Hollow marks had not been published yet.
        </span>
        <label className="flex items-center gap-2 text-sm">
          <span className="font-medium">Show what was known on</span>
          <input
            type="date"
            value={dayOf(cutoffMs)}
            min={dayOf(rail.domain[0])}
            max={dayOf(rail.domain[1])}
            onChange={(e) => {
              const t = endOfDayMs(e.target.value);
              if (Number.isFinite(t)) onCutoff(t);
            }}
            className="num h-9 border border-border bg-surface-raised px-2 text-sm outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
          />
        </label>
      </figcaption>

      <div className="overflow-x-auto border border-border bg-surface-raised">
        <svg
          ref={svg}
          viewBox={`0 0 ${WIDTH} ${rail.height}`}
          className="block h-auto min-w-[640px] w-full touch-none select-none"
          role="group"
          aria-label="Filings by the date each became known"
          onPointerDown={(e) => {
            if ((e.target as Element).closest("[data-mark]")) return;
            (e.currentTarget as Element).setPointerCapture(e.pointerId);
            fromPointer(e);
          }}
          onPointerMove={(e) => {
            if (e.buttons === 1 && (e.currentTarget as Element).hasPointerCapture(e.pointerId)) fromPointer(e);
          }}
        >
          {rail.lanes.map((l) => (
            <g key={l.family}>
              <line x1={rail.plot.left} x2={rail.plot.right} y1={l.y} y2={l.y} className="stroke-border-soft" />
              <text x={rail.plot.left - 10} y={l.y} textAnchor="end" dominantBaseline="middle" className="fill-text-muted text-[12px]">
                {FAMILY_LABEL[l.family]}
              </text>
            </g>
          ))}
          {rail.ticks.map((t) => (
            <g key={`${t.label}-${t.x}`}>
              <line x1={t.x} x2={t.x} y1={rail.plot.top} y2={rail.plot.bottom} className={t.major ? "stroke-border" : "stroke-border-soft"} strokeDasharray={t.major ? undefined : "2 4"} />
              <text x={t.x + 3} y={rail.plot.bottom + 16} className={cn("num text-xs", t.major ? "fill-text font-semibold" : "fill-text-muted")}>
                {t.label}
              </text>
            </g>
          ))}

          {rail.links.map((l) => (
            <path
              key={`${l.from}-${l.to}`}
              d={`M ${l.fromX} ${laneY(l.fromLane)} C ${l.fromX - 24} ${laneY(l.fromLane) - 16}, ${l.toX + 24} ${laneY(l.toLane) - 16}, ${l.toX} ${laneY(l.toLane)}`}
              fill="none"
              strokeDasharray="4 3"
              className={cn("stroke-unconfirmed", rail.marks.find((m) => m.id === l.from)?.known ? "opacity-100" : "opacity-40")}
            />
          ))}

          {rail.marks.map((m) => {
            const y = laneY(m.lane);
            const selected = m.id === selectedId;
            const quarantined = m.filing.quarantined;
            return (
              <g
                key={m.id}
                data-mark
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                aria-label={`${m.filing.form ?? "Document"} ${m.filing.fiscal_period ?? ""}, known ${formatStamp(m.filing.known_at)}, ${m.known ? "visible" : "not yet published"} at the chosen date${quarantined ? ", could not be read" : ""}`}
                onClick={() => onSelect(m.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(m.id);
                  }
                }}
                className="cursor-pointer outline-none [&:focus-visible>.ring]:opacity-100"
              >
                <title>{`${m.filing.form ?? "Document"} · known ${formatStamp(m.filing.known_at)}`}</title>
                {m.tailX != null ? <line x1={m.tailX} x2={m.x} y1={y} y2={y} className="stroke-text-muted" strokeWidth={2} /> : null}
                <circle className="ring fill-none stroke-focus opacity-0" cx={m.x} cy={y} r={11} strokeWidth={2} />
                {quarantined ? (
                  <path d={`M ${m.x - 6} ${y - 6} L ${m.x + 6} ${y + 6} M ${m.x + 6} ${y - 6} L ${m.x - 6} ${y + 6}`} className="stroke-warning-signal" strokeWidth={2.5} fill="none" />
                ) : (
                  <circle
                    cx={m.x}
                    cy={y}
                    r={selected ? 8 : 6}
                    strokeWidth={2}
                    className={cn(m.known ? "fill-accent stroke-accent" : "fill-surface-raised stroke-text-muted", selected && "stroke-text")}
                  />
                )}
              </g>
            );
          })}

          <g
            role="slider"
            tabIndex={0}
            aria-label="Cutoff date"
            aria-valuemin={rail.domain[0]}
            aria-valuemax={rail.domain[1]}
            aria-valuenow={Math.round(cutoffMs)}
            aria-valuetext={formatDay(new Date(cutoffMs).toISOString())}
            onKeyDown={onKey}
            className="cursor-ew-resize outline-none [&:focus-visible>.grip]:stroke-focus [&:focus-visible>.grip]:stroke-[3]"
          >
            <line x1={rail.cutoffX} x2={rail.cutoffX} y1={rail.plot.top - 2} y2={rail.plot.bottom + 2} className="stroke-text" strokeWidth={2} />
            <rect className="grip fill-text stroke-text" x={rail.cutoffX - 5} y={rail.plot.top - 6} width={10} height={14} />
          </g>
        </svg>
      </div>
      <p className="text-xs text-text-muted">
        Drag the line, or focus it and use the arrow keys (Shift for 30 days). A thin tail means the filing was dated earlier than the moment it counts as known. A dashed
        link joins an amendment to the filing it restates.
      </p>
    </figure>
  );
}
