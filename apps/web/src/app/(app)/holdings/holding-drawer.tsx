"use client";

import { LoaderCircleIcon, PencilIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ScoreMark } from "@/components/insight/score-mark";
import { SideDrawer } from "@/components/insight/side-drawer";
import { StepSparkline } from "@/components/insight/step-line";
import { BandBadge } from "@/components/portfolio/band-badge";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type NewsItemOut } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney, formatPercent, formatPp, formatPrice, formatQuantity, gainLossTone } from "@/lib/format";
import { AXIS_LABEL, MARK_AXES, petalsFromMap } from "@/lib/insight/insight";
import { toNum, type HoldingInsight, type HoldingNote } from "@/lib/portfolio/holding-columns";
import { displayTicker } from "@/lib/portfolio/holdings-view";
import { cn } from "@/lib/utils";

/*
 * Holding drawer (insight plan I9.4): opened from a holdings row, with Overview, Position, Updates
 * and Notes tabs and a link to the full holding page. Notes are stored exactly as written.
 */

export type DrawerTabKey = "overview" | "position" | "updates" | "notes";
const TONE = { gain: "text-gain", loss: "text-loss", flat: "" };

function Fact({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="border-b border-border-soft py-2">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num mt-0.5 text-sm">
        {children}
        {sub ? <span className="block text-xs text-text-muted">{sub}</span> : null}
      </dd>
    </div>
  );
}

function Overview({ h, row }: { h: Holding; row: HoldingInsight | undefined }) {
  const upside = toNum(row?.fair_upside);
  const fair = toNum(row?.fair_value);
  const history = (row?.fair_history ?? []).map((p) => ({ date: p.as_of, value: null, fair: toNum(p.value) }));
  const petals = petalsFromMap(row?.mark);
  return (
    <div className="space-y-5">
      <section aria-labelledby="dr-thesis">
        <h3 id="dr-thesis" className="text-sm font-semibold">
          Why you own it
        </h3>
        {row?.thesis ? (
          <p className="mt-1 max-w-prose text-sm">{row.thesis}</p>
        ) : (
          <p className="mt-1 text-sm text-text-muted">
            No thesis written yet.{" "}
            <Link href={`/companies/${encodeURIComponent(h.ticker)}`} className="text-accent-ink underline underline-offset-2">
              Write one on the holding page
            </Link>
          </p>
        )}
      </section>
      <section aria-labelledby="dr-score" className="flex items-center gap-4">
        <ScoreMark petals={petals} size="header" label={h.name ?? h.ticker} />
        <div className="min-w-0">
          <h3 id="dr-score" className="text-sm font-semibold">
            Score
          </h3>
          <ul className="num mt-1 grid grid-cols-2 gap-x-4 text-xs sm:grid-cols-3">
            {MARK_AXES.map((a) => {
              const v = toNum(row?.mark?.[a]);
              return (
                <li key={a}>
                  <span className="text-text-muted">{AXIS_LABEL[a]}</span> {v === null ? "unknown" : `${v} of 6`}
                </li>
              );
            })}
          </ul>
          <Link href={`/companies/${encodeURIComponent(h.ticker)}/checks`} className="text-xs text-accent-ink underline underline-offset-2">
            See the checks
          </Link>
        </div>
      </section>
      <section aria-labelledby="dr-fair">
        <h3 id="dr-fair" className="text-sm font-semibold">
          Price against fair value
        </h3>
        {fair !== null ? (
          <div className="mt-1 flex items-center justify-between gap-3">
            <p className="text-sm">
              <span className="num font-semibold">{formatPrice(fair, h.instrument_currency)}</span>{" "}
              <span className="text-text-muted">{row?.fair_basis}</span>
              {upside !== null ? (
                <span className={cn("num block text-xs", upside < 0 ? "text-loss" : "text-gain")}>
                  {upside < 0 ? `price ${formatPercent(-upside, { decimals: 0 })} above` : `price ${formatPercent(upside, { decimals: 0 })} below`}
                  {row?.fair_stale ? " · stale" : ""}
                </span>
              ) : null}
            </p>
            <StepSparkline rows={history} label={`${displayTicker(h.ticker)} recorded fair values`} />
          </div>
        ) : (
          <p className="mt-1 text-sm text-text-muted">{row?.fair_reason ?? "No fair value recorded."}</p>
        )}
      </section>
      <section aria-labelledby="dr-last">
        <h3 id="dr-last" className="text-sm font-semibold">
          Latest event
        </h3>
        <LastEventLine row={row} />
      </section>
    </div>
  );
}

export function LastEventLine({ row, compact }: { row: HoldingInsight | undefined; compact?: boolean }) {
  const e = row?.last_event;
  if (!e) return <span className={cn("text-text-muted", compact ? "text-xs" : "mt-1 block text-sm")}>Nothing recorded</span>;
  const text = (
    <>
      <span className="num text-text-muted">{formatDate(e.date)}</span> {e.title}
    </>
  );
  return (
    <span className={cn(compact ? "line-clamp-2 text-xs" : "mt-1 block text-sm")}>
      {e.href ? (
        <Link href={e.href} className="hover:text-accent-ink hover:underline">
          {text}
        </Link>
      ) : (
        text
      )}
    </span>
  );
}

function Position({ h, row, baseCcy }: { h: Holding; row: HoldingInsight | undefined; baseCcy: string }) {
  const next = row?.next_payment;
  const yld = toNum(row?.yield_on_price);
  const yoc = toNum(row?.yield_on_cost);
  const inc = toNum(row?.income_12m);
  return (
    <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      <Fact label="Quantity">{formatQuantity(h.quantity)}</Fact>
      <Fact label="Average price">{formatPrice(h.avg_price, h.instrument_currency)}</Fact>
      <Fact label="Price">{formatPrice(h.current_price, h.instrument_currency)}</Fact>
      <Fact label={`Value (${baseCcy})`}>{formatMoney(h.value_base, baseCcy, { decimals: 0 })}</Fact>
      <Fact label="Weight" sub={h.target_weight !== null ? `target ${formatPercent(h.target_weight)}` : "no target"}>
        {formatPercent(h.weight)}
      </Fact>
      <Fact label="Drift">
        <span className="inline-flex items-center gap-2">
          {h.drift_status !== "no_target" ? formatPp(h.drift) : null}
          <BandBadge status={h.drift_status} />
        </span>
      </Fact>
      <Fact label="Unrealised P/L" sub={formatPercent(h.ppl_pct, { signed: true })}>
        <span className={TONE[gainLossTone(h.ppl)]}>{formatMoney(h.ppl, h.account_currency, { decimals: 0, signed: true })}</span>
      </Fact>
      <Fact label="Dividend yield" sub={yoc !== null ? `${formatPercent(yoc)} on cost` : undefined}>
        {yld !== null ? formatPercent(yld) : row?.pays_dividend === false ? "Pays no dividend" : "—"}
      </Fact>
      <Fact label="Next payment" sub={next ? `${next.kind}, ${formatPrice(toNum(next.per_share), next.currency ?? h.instrument_currency)} a share` : undefined}>
        {/* The amount is converted to the account currency; only the per-share figure is in the dividend's own. */}
        {next ? `${formatDate(next.date)}${toNum(next.amount) !== null ? `, ${formatMoney(toNum(next.amount), row?.income_currency ?? h.account_currency ?? baseCcy)}` : ""}` : "—"}
      </Fact>
      <Fact label="Income, next 12 months">{inc !== null ? formatMoney(inc, row?.income_currency ?? baseCcy, { decimals: 0 }) : "—"}</Fact>
    </dl>
  );
}

function Updates({ ticker, row }: { ticker: string; row: HoldingInsight | undefined }) {
  const [items, setItems] = useState<NewsItemOut[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    getBrowserApiClient()
      .GET("/news", { params: { query: { ticker, direct: true, limit: 8 } } })
      .then(({ data }) => {
        if (!live) return;
        if (data) setItems(data.items);
        else setFailed(true);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [ticker]);
  return (
    <div className="space-y-4">
      <section aria-labelledby="dr-up-last">
        <h3 id="dr-up-last" className="text-sm font-semibold">
          Latest event
        </h3>
        <LastEventLine row={row} />
      </section>
      <section aria-labelledby="dr-up-news">
        <h3 id="dr-up-news" className="text-sm font-semibold">
          News
        </h3>
        {failed ? (
          <p className="mt-1 text-sm text-text-muted">The news could not be loaded.</p>
        ) : items === null ? (
          <p className="mt-1 flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" /> Loading news…
          </p>
        ) : items.length === 0 ? (
          <p className="mt-1 text-sm text-text-muted">No linked news stored for this holding.</p>
        ) : (
          <ul className="mt-1 divide-y divide-border-soft">
            {items.map((n) => (
              <li key={n.id} className="py-2 text-sm">
                <a href={n.url} target="_blank" rel="noreferrer" className="hover:text-accent-ink hover:underline">
                  {n.headline}
                </a>
                <span className="block text-xs text-text-muted">
                  {n.outlet_name ?? n.outlet ?? "Unknown outlet"}, {formatDate(n.published_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <Link href={`/news?ticker=${encodeURIComponent(ticker)}`} className="mt-2 inline-block text-xs text-accent-ink underline underline-offset-2">
          All news for {displayTicker(ticker)}
        </Link>
      </section>
    </div>
  );
}

function Notes({ ticker, onCount }: { ticker: string; onCount: (n: number) => void }) {
  const [notes, setNotes] = useState<HoldingNote[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const path = { params: { path: { ticker } } };

  const load = useCallback(async () => {
    const { data } = await getBrowserApiClient().GET("/holdings/{ticker}/notes", { params: { path: { ticker } } });
    if (data) {
      setNotes(data);
      onCount(data.length);
    } else setError("The notes could not be loaded.");
  }, [ticker, onCount]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the notes once per ticker
    void load();
  }, [load]);

  async function run(op: () => Promise<{ error?: unknown }>, failure: string) {
    setBusy(true);
    setError(null);
    const { error: e } = await op();
    setBusy(false);
    if (e) setError(failure);
    else await load();
    return !e;
  }

  return (
    <div className="space-y-4">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!draft.trim()) return;
          const ok = await run(() => getBrowserApiClient().POST("/holdings/{ticker}/notes", { ...path, body: { body: draft } }), "The note was not saved.");
          if (ok) setDraft("");
        }}
        className="space-y-2"
      >
        <label htmlFor="note-new" className="text-sm font-semibold">
          Add a note
        </label>
        <textarea
          id="note-new"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          maxLength={20000}
          className="block w-full border border-border bg-surface px-2 py-1.5 text-sm focus-visible:outline-2 focus-visible:outline-accent"
          placeholder="What you noticed, what to check next time…"
        />
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-text-muted">Saved as written. The assistant can search your notes but never edits them.</p>
          <Button type="submit" size="sm" disabled={busy || !draft.trim()}>
            Save note
          </Button>
        </div>
      </form>
      {error ? (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      ) : null}
      {notes === null ? (
        <p className="text-sm text-text-muted" role="status">
          Loading notes…
        </p>
      ) : notes.length === 0 ? (
        <p className="text-sm text-text-muted">No notes yet.</p>
      ) : (
        <ul className="divide-y divide-border-soft border-t border-border-soft">
          {notes.map((n) => (
            <li key={n.id} className="py-3">
              {editing?.id === n.id ? (
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const ok = await run(
                      () =>
                        getBrowserApiClient().PUT("/holdings/{ticker}/notes/{note_id}", {
                          params: { path: { ticker, note_id: n.id } },
                          body: { body: editing.body },
                        }),
                      "The note was not saved.",
                    );
                    if (ok) setEditing(null);
                  }}
                  className="space-y-2"
                >
                  <label htmlFor={`note-${n.id}`} className="sr-only">
                    Edit note
                  </label>
                  <textarea
                    id={`note-${n.id}`}
                    value={editing.body}
                    onChange={(e) => setEditing({ id: n.id, body: e.target.value })}
                    rows={4}
                    className="block w-full border border-border bg-surface px-2 py-1.5 text-sm"
                  />
                  <div className="flex justify-end gap-2">
                    <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                    <Button type="submit" size="sm" disabled={busy || !editing.body.trim()}>
                      Save changes
                    </Button>
                  </div>
                </form>
              ) : (
                <>
                  <p className="max-w-prose text-sm whitespace-pre-wrap">{n.body}</p>
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <span className="text-xs text-text-muted">
                      {n.updated_at !== n.created_at ? `Edited ${formatDateTime(n.updated_at)}` : formatDateTime(n.created_at)}
                    </span>
                    <span className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => setEditing({ id: n.id, body: n.body })}>
                        <PencilIcon aria-hidden="true" /> Edit
                      </Button>
                      {confirm === n.id ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={async () => {
                            await run(
                              () => getBrowserApiClient().DELETE("/holdings/{ticker}/notes/{note_id}", { params: { path: { ticker, note_id: n.id } } }),
                              "The note was not deleted.",
                            );
                            setConfirm(null);
                          }}
                        >
                          Delete for good
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => setConfirm(n.id)}>
                          <Trash2Icon aria-hidden="true" /> Delete
                        </Button>
                      )}
                    </span>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function HoldingDrawer({
  holding,
  row,
  baseCcy,
  tab,
  onTab,
  onClose,
  onNotes,
}: {
  holding: Holding | null;
  row: HoldingInsight | undefined;
  baseCcy: string;
  tab: DrawerTabKey;
  onTab: (t: DrawerTabKey) => void;
  onClose: () => void;
  onNotes: (ticker: string, n: number) => void;
}) {
  const ticker = holding?.ticker ?? "";
  const countNotes = useCallback((n: number) => onNotes(ticker, n), [onNotes, ticker]);
  if (!holding) return <SideDrawer open={false} onClose={onClose} title="" tabs={[]} tab="" onTab={() => {}} />;
  return (
    <SideDrawer
      open
      onClose={onClose}
      title={holding.name ?? holding.ticker}
      subtitle={<span className="font-mono">{displayTicker(holding.ticker)}</span>}
      fullPageHref={`/companies/${encodeURIComponent(holding.ticker)}`}
      tab={tab}
      onTab={(k) => onTab(k as DrawerTabKey)}
      tabs={[
        { key: "overview", label: "Overview", content: <Overview h={holding} row={row} /> },
        { key: "position", label: "Position", content: <Position h={holding} row={row} baseCcy={baseCcy} /> },
        { key: "updates", label: "Updates", content: <Updates ticker={holding.ticker} row={row} /> },
        { key: "notes", label: `Notes${row?.notes ? ` (${row.notes})` : ""}`, content: <Notes ticker={holding.ticker} onCount={countNotes} /> },
      ]}
    />
  );
}
