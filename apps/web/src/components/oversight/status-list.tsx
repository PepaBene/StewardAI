"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { CheckStateIcon } from "@/components/insight/check-list";
import { Button } from "@/components/ui/button";
import { STATE_LABEL, type CheckState } from "@/lib/insight/insight";

/*
 * StatusList: the System pages' version of CheckList rows (plan §5.0 translation rules). An icon
 * and a word per state, a bold lead-in, one sentence with the figures, an optional action and a
 * collapsed Details. Failed rows come first and are always open; the rest are paged.
 */

export type StatusRow = {
  id: string;
  state: CheckState;
  /** The bold name: a gate, a feature, an endpoint (a name, never an id). */
  lead: string;
  sentence: ReactNode;
  /** Raw HTTP status, provider messages, ids: collapsed by default. */
  details?: ReactNode;
  /** A control for the row (a button). */
  action?: ReactNode;
  href?: string;
  hrefLabel?: string;
};

function Row({ row }: { row: StatusRow }) {
  return (
    <li className="flex gap-2.5 border-t border-border-soft py-2.5 first:border-t-0">
      <CheckStateIcon state={row.state} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug">
          <span className="sr-only">{STATE_LABEL[row.state]}: </span>
          <span className="font-semibold">{row.lead}</span>
          <span className="text-text-muted"> · </span>
          {row.sentence}
        </p>
        {row.details ? (
          <details className="mt-1 text-xs text-text-muted">
            <summary className="cursor-pointer">Details</summary>
            <div className="mt-1 space-y-0.5 break-words">{row.details}</div>
          </details>
        ) : null}
      </div>
      {row.action ? <div className="shrink-0 self-start">{row.action}</div> : null}
      {row.href ? (
        <Link href={row.href} className="shrink-0 self-start text-xs text-accent-ink underline underline-offset-2">
          {row.hrefLabel ?? "See details"}
        </Link>
      ) : null}
    </li>
  );
}

const ORDER: CheckState[] = ["fail", "pass", "unknown", "na"];
const GROUP_WORD: Record<"unknown" | "na", string> = {
  unknown: "could not be measured",
  na: "do not apply",
};

export function StatusList({
  rows,
  caption,
  pageSize = 25,
  passWord = "pass",
  className,
}: {
  rows: readonly StatusRow[];
  /** Names the list for assistive tech. */
  caption: string;
  pageSize?: number;
  /** "pass", "are on", "are met": the verb in "n of m pass". */
  passWord?: string;
  className?: string;
}) {
  const [shown, setShown] = useState(pageSize);
  const counts: Record<CheckState, number> = { pass: 0, fail: 0, unknown: 0, na: 0 };
  for (const r of rows) counts[r.state] += 1;
  const sorted = ORDER.flatMap((s) => rows.filter((r) => r.state === s));
  const open = sorted.filter((r) => r.state === "fail" || r.state === "pass");
  const visible = open.slice(0, Math.max(shown, counts.fail));
  const more = open.length - visible.length;
  const applicable = counts.pass + counts.fail;
  return (
    <div className={className}>
      <p className="mb-1 text-xs text-text-muted">
        <span className="num font-medium text-text">
          {counts.pass} of {applicable}
        </span>{" "}
        {passWord}
        {counts.unknown ? ` · ${counts.unknown} could not be measured` : ""}
      </p>
      <ul aria-label={caption}>
        {visible.map((r) => (
          <Row key={r.id} row={r} />
        ))}
      </ul>
      {more > 0 ? (
        <Button variant="ghost" size="sm" onClick={() => setShown((n) => n + pageSize)}>
          Show {Math.min(more, pageSize)} more
        </Button>
      ) : null}
      {(["unknown", "na"] as const).map((s) => {
        const list = sorted.filter((r) => r.state === s);
        return list.length ? (
          <details key={s} className="border-t border-border-soft py-2">
            <summary className="cursor-pointer text-xs text-text-muted">
              {list.length} {GROUP_WORD[s]}
            </summary>
            <ul className="mt-1">
              {list.map((r) => (
                <Row key={r.id} row={r} />
              ))}
            </ul>
          </details>
        ) : null;
      })}
    </div>
  );
}
