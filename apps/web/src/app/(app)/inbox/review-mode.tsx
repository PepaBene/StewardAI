"use client";

import { ChevronLeftIcon, ChevronRightIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { EntityName } from "@/components/entity-name";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ageTone, KIND_LABELS, type QueueRow } from "@/lib/inbox-queue";
import { cn } from "@/lib/utils";

/** A horizontal swipe longer than this (px) steps to the next or previous item. */
const SWIPE_PX = 60;

/**
 * Inbox review mode (plan §5.8): one item per screen, oldest first. Swipe or use the buttons
 * to move; "Open" goes to the item where it is decided. Built on <dialog>, so Escape closes it.
 */
export function ReviewMode({ rows, open, onClose }: { rows: readonly QueueRow[]; open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [index, setIndex] = useState(0);
  const touchX = useRef<number | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  const count = rows.length;
  const at = Math.min(index, Math.max(0, count - 1));
  const row = rows[at];
  const step = (by: number) => setIndex((i) => Math.min(Math.max(0, i + by), Math.max(0, count - 1)));
  const tone = row ? ageTone(row.ageDays) : "none";

  return (
    <dialog
      ref={ref}
      aria-labelledby="review-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") step(1);
        if (e.key === "ArrowLeft") step(-1);
      }}
      className="m-0 h-dvh max-h-dvh w-full max-w-full bg-page p-0 text-text backdrop:bg-overlay md:m-auto md:h-auto md:max-h-[85dvh] md:w-[min(32rem,92vw)] md:rounded-card md:shadow-popover"
    >
      {open ? (
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-3 px-4 pt-4 pb-2">
            <h2 id="review-title" className="text-[15px] font-semibold">
              Review one at a time
            </h2>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close review">
              <XIcon aria-hidden="true" />
            </Button>
          </div>
          {/* Progress: one segment per item, filled up to the current one. */}
          <div className="flex gap-0.5 px-4" aria-hidden="true">
            {rows.slice(0, 40).map((r, i) => (
              <span key={r.id} className={cn("h-1 flex-1 rounded-full", i <= at ? "bg-accent" : "bg-surface-muted")} />
            ))}
          </div>
          <p className="num px-4 pt-2 text-xs text-text-muted" aria-live="polite">
            {count === 0 ? "Nothing waiting" : `${at + 1} of ${count}`}
          </p>

          {row ? (
            <article
              className="mx-4 mt-4 flex flex-1 flex-col rounded-card bg-surface p-5 md:flex-none"
              onTouchStart={(e) => (touchX.current = e.touches[0]?.clientX ?? null)}
              onTouchEnd={(e) => {
                const start = touchX.current;
                const end = e.changedTouches[0]?.clientX;
                touchX.current = null;
                if (start == null || end == null) return;
                if (end - start < -SWIPE_PX) step(1);
                if (end - start > SWIPE_PX) step(-1);
              }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="kind">{KIND_LABELS[row.kind]}</Badge>
                {row.ageDays !== null ? (
                  <span className={cn("num text-xs", tone === "loss" ? "text-loss" : tone === "warning" ? "text-warning-signal" : "text-text-faint")}>
                    {row.ageDays === 0 ? "Today" : `Waiting ${row.ageDays} ${row.ageDays === 1 ? "day" : "days"}`}
                  </span>
                ) : null}
              </div>
              {row.ticker ? (
                <p className="mt-4 text-sm text-text-muted">
                  <EntityName id={row.ticker} name={row.name} chip={false} />
                </p>
              ) : null}
              <p className="mt-1 font-heading text-[22px] leading-snug font-semibold">{row.headline}</p>
              {row.why ? <p className="mt-3 text-sm text-text-muted">{row.why}</p> : null}
              <div className="mt-auto pt-6">
                <Button asChild className="w-full">
                  <Link href={row.href} onClick={onClose}>
                    Open and decide
                  </Link>
                </Button>
              </div>
            </article>
          ) : (
            <p className="mx-4 mt-4 rounded-card bg-surface p-6 text-sm text-text-muted">Every queue is clear.</p>
          )}

          <div className="flex gap-2 p-4">
            <Button variant="outline" className="h-11 flex-1" onClick={() => step(-1)} disabled={at === 0}>
              <ChevronLeftIcon aria-hidden="true" />
              Previous
            </Button>
            <Button variant="outline" className="h-11 flex-1" onClick={() => step(1)} disabled={at >= count - 1}>
              Skip
              <ChevronRightIcon aria-hidden="true" />
            </Button>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
