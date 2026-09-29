"use client";

import { LoaderCircleIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type WatchlistItem } from "@/lib/api/client";

type Draft = { ticker: string; list_name: string; buy_target: string; sell_target: string; reason: string; note: string };
const emptyDraft = (): Draft => ({ ticker: "", list_name: "Default", buy_target: "", sell_target: "", reason: "", note: "" });
const fetchWatchlist = () => getBrowserApiClient().GET("/watchlist");
const fetchHoldings = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 7 } } });
const textAreaClass = "min-h-[68px] w-full resize-y border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

function latestPrice(value: number) {
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(value);
}

function detailMessage(error: unknown, fallback: string) {
  return error && typeof error === "object" && "detail" in error && typeof error.detail === "string" ? error.detail : fallback;
}

export function WatchlistView() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [quotes, setQuotes] = useState<Map<string, number>>(new Map());

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchWatchlist();
      if (result.response.ok && result.data) { setItems(result.data); setError(""); }
      else setError(detailMessage(result.error, "Could not load the watchlist. Check that the API and database are available."));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the watchlist.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    let current = true;
    fetchWatchlist().then((result) => {
      if (!current) return;
      if (result.response.ok && result.data) { setItems(result.data); setError(""); }
      else setError(detailMessage(result.error, "Could not load the watchlist. Check that the API and database are available."));
      setLoading(false);
    }).catch((cause: unknown) => {
      if (!current) return;
      setError(cause instanceof Error ? cause.message : "Could not load the watchlist.");
      setLoading(false);
    });
    void fetchHoldings().then((result) => {
      if (result.data?.holdings) setQuotes(new Map(result.data.holdings.flatMap((row) => {
        const price = row.intraday?.price ?? row.last_close;
        return row.ticker && price != null ? [[row.ticker.toUpperCase(), price] as [string, number]] : [];
      })));
    }).catch(() => undefined);
    return () => { current = false; };
  }, []);

  async function addItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const payload = {
      ticker: draft.ticker.trim().toUpperCase(), list_name: draft.list_name.trim() || "Default",
      buy_target: draft.buy_target ? Number(draft.buy_target) : null,
      sell_target: draft.sell_target ? Number(draft.sell_target) : null,
      reason: draft.reason.trim() || null, note: draft.note.trim() || null,
    };
    try {
      const result = await getBrowserApiClient().POST("/watchlist", { body: payload });
      if (!result.response.ok) setError(detailMessage(result.error, "Could not save this ticker."));
      else { setDraft(emptyDraft()); await reload(); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save this ticker."); }
    setSaving(false);
  }

  async function saveItem(item: WatchlistItem, form: HTMLFormElement) {
    setSaving(true); setError("");
    const data = new FormData(form);
    const body = {
      list_name: String(data.get("list_name") || "Default"),
      buy_target: data.get("buy_target") ? Number(data.get("buy_target")) : null,
      sell_target: data.get("sell_target") ? Number(data.get("sell_target")) : null,
      reason: String(data.get("reason") || "").trim() || null,
      note: String(data.get("note") || "").trim() || null,
    };
    try {
      const result = await getBrowserApiClient().PATCH("/watchlist/{item_id}", { params: { path: { item_id: item.id } }, body });
      if (!result.response.ok) setError(detailMessage(result.error, `Could not update ${item.ticker}.`));
      else await reload();
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not update ${item.ticker}.`); }
    setSaving(false);
  }

  async function removeItem(item: WatchlistItem) {
    setSaving(true); setError("");
    try {
      const result = await getBrowserApiClient().DELETE("/watchlist/{item_id}", { params: { path: { item_id: item.id } } });
      if (!result.response.ok) setError(detailMessage(result.error, `Could not remove ${item.ticker}.`));
      else await reload();
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not remove ${item.ticker}.`); }
    setSaving(false);
  }

  return (
    <div className="space-y-6">
      <PageHeader kicker="Research" title="Watchlist" description="Track your thesis, price targets, and notes separately from positions you own." />
      {error ? <AlertMessage title="Watchlist needs attention">{error}</AlertMessage> : null}

      <Blueprint className="space-y-4 p-5">
        <PanelHeading kicker="Research list" title="Add a ticker" />
        <form onSubmit={addItem} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Ticker" id="add-ticker"><Input id="add-ticker" required maxLength={32} placeholder="AAPL" value={draft.ticker} onChange={(event) => setDraft({ ...draft, ticker: event.target.value })} /></Field>
            <Field label="List name" id="add-list-name"><Input id="add-list-name" maxLength={80} placeholder="Default" value={draft.list_name} onChange={(event) => setDraft({ ...draft, list_name: event.target.value })} /></Field>
            <Field label="Buy target" id="add-buy-target"><Input id="add-buy-target" type="number" min="0" step="any" placeholder="Optional" value={draft.buy_target} onChange={(event) => setDraft({ ...draft, buy_target: event.target.value })} /></Field>
            <Field label="Sell target" id="add-sell-target"><Input id="add-sell-target" type="number" min="0" step="any" placeholder="Optional" value={draft.sell_target} onChange={(event) => setDraft({ ...draft, sell_target: event.target.value })} /></Field>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Reason" id="add-reason"><textarea id="add-reason" className={textAreaClass} maxLength={2000} placeholder="What makes this company worth watching?" value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} /></Field>
            <Field label="Note" id="add-note"><textarea id="add-note" className={textAreaClass} maxLength={5000} placeholder="Add a note for your next review" value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} /></Field>
          </div>
          <div className="flex justify-end"><Button disabled={saving || !draft.ticker.trim()}><PlusIcon aria-hidden="true" /> Add to watchlist</Button></div>
        </form>
      </Blueprint>

      <Blueprint className="min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border px-5 py-4">
          <PanelHeading kicker="Saved ideas" title="Your watchlist" />
          <span className="kicker">{items.length} {items.length === 1 ? "ticker" : "tickers"}</span>
        </div>
        {loading ? <p className="flex items-center gap-2 px-5 py-7 text-sm text-text-muted" role="status"><LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />Loading watchlist…</p> : items.length === 0 ? (
          <div className="p-5"><EmptyState title="No tickers yet">Add a company above to start a research thesis and track the prices that matter to you.</EmptyState></div>
        ) : (
          <div className="overflow-x-auto">
            <div className="min-w-[1160px]">
              <div className="grid grid-cols-[130px_100px_130px_130px_minmax(190px,1fr)_minmax(190px,1fr)_88px] gap-3 border-b border-border bg-surface-muted px-5 py-2.5 text-xs font-medium text-text-muted">
                <span>Ticker / list</span><span className="text-right">Latest price</span><span>Buy target</span><span>Sell target</span><span>Reason</span><span>Note</span><span className="sr-only">Actions</span>
              </div>
              <div className="divide-y divide-border">
                {items.map((item) => (
                  <form key={item.id} onSubmit={(event) => { event.preventDefault(); void saveItem(item, event.currentTarget); }} className="grid grid-cols-[130px_100px_130px_130px_minmax(190px,1fr)_minmax(190px,1fr)_88px] items-start gap-3 px-5 py-3">
                    <div className="min-w-0 pt-1"><div className="font-mono text-sm font-semibold text-text">{item.ticker}</div><div className="mt-0.5 truncate text-xs text-text-muted">{item.list_name}</div><input type="hidden" name="list_name" value={item.list_name} /></div>
                    <div className="num pt-1 text-right font-mono text-sm">{quotes.has(item.ticker.toUpperCase()) ? latestPrice(quotes.get(item.ticker.toUpperCase())!) : <span className="text-xs text-text-faint">—</span>}</div>
                    <label className="sr-only" htmlFor={`buy-${item.id}`}>Buy target for {item.ticker}</label><Input id={`buy-${item.id}`} name="buy_target" type="number" min="0" step="any" placeholder="Set target" defaultValue={item.buy_target ?? ""} />
                    <label className="sr-only" htmlFor={`sell-${item.id}`}>Sell target for {item.ticker}</label><Input id={`sell-${item.id}`} name="sell_target" type="number" min="0" step="any" placeholder="Set target" defaultValue={item.sell_target ?? ""} />
                    <label className="sr-only" htmlFor={`reason-${item.id}`}>Reason for watching {item.ticker}</label><textarea id={`reason-${item.id}`} name="reason" className={textAreaClass} rows={2} placeholder="Why this company?" defaultValue={item.reason ?? ""} />
                    <label className="sr-only" htmlFor={`note-${item.id}`}>Note for {item.ticker}</label><textarea id={`note-${item.id}`} name="note" className={textAreaClass} rows={2} placeholder="Research note" defaultValue={item.note ?? ""} />
                    <div className="flex items-center justify-end gap-1">
                      <Button type="submit" size="sm" variant="outline" aria-label={`Save ${item.ticker}`} title="Save changes" disabled={saving}>Save</Button>
                      <Button type="button" size="icon" variant="ghost" aria-label={`Remove ${item.ticker}`} title="Remove ticker" disabled={saving} onClick={() => void removeItem(item)}><Trash2Icon aria-hidden="true" /></Button>
                    </div>
                  </form>
                ))}
              </div>
            </div>
          </div>
        )}
      </Blueprint>
      {items.length > 0 ? <p className="text-xs text-text-muted">Prices use the latest stored quote for securities already in your holdings. Other tickers are shown without a quote.</p> : null}
    </div>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return <div className="space-y-1.5"><Label htmlFor={id} className="text-xs text-text-muted">{label}</Label>{children}</div>;
}
