"use client";

import { useCallback, useEffect, useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import type { ProposedRequest } from "@/lib/xray/xray";

type Item = { id: number; name: string; note?: string | null; created_at: string };

const BUTTON =
  "inline-flex h-9 items-center border border-border px-3 text-sm hover:bg-surface-muted disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

/** Keep a what-if by name and open or remove the ones kept. Only the weights are stored; opening
 * one puts them back in the editor, and "Show before and after" runs them on today's holdings. */
export function SavedScenarios({
  request,
  onOpen,
}: {
  request: ProposedRequest | null;
  onOpen: (scenario: ProposedRequest, name: string) => void;
}) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const res = await getBrowserApiClient().GET("/portfolio/xray/scenarios");
    if (res.response.ok && res.data) setItems(res.data);
    else setProblem("Your saved what-ifs could not be loaded.");
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the list once on mount
    void refresh();
  }, [refresh]);

  async function save() {
    if (!request) return setProblem("Change a weight or add a security first, then save it.");
    if (name.trim() === "") return setProblem("Give the what-if a name.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/xray/scenarios", { body: { name: name.trim(), scenario: request } });
    setBusy(false);
    if (!res.response.ok) return setProblem("The what-if could not be saved.");
    setName("");
    await refresh();
  }

  async function open(id: number, label: string) {
    const res = await getBrowserApiClient().GET("/portfolio/xray/scenarios/{scenario_id}", { params: { path: { scenario_id: id } } });
    if (res.response.ok && res.data) onOpen(res.data.scenario, label);
    else setProblem("That what-if could not be opened.");
  }

  async function remove(id: number) {
    const res = await getBrowserApiClient().DELETE("/portfolio/xray/scenarios/{scenario_id}", { params: { path: { scenario_id: id } } });
    if (!res.response.ok) return setProblem("That what-if could not be removed.");
    await refresh();
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Saved" title="Your what-ifs" />
      <form
        className="mt-3 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label htmlFor="scenario-name" className="sr-only">Name for this what-if</label>
        <input
          id="scenario-name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name, for example Trim Apple"
          className="h-9 w-64 border border-border bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
        />
        <button type="submit" disabled={busy} className={BUTTON}>Save this what-if</button>
      </form>
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
      {items && items.length > 0 ? (
        <ul className="mt-3 divide-y divide-border-soft border border-border">
          {items.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-sm">
              <span>
                {i.name}
                <span className="ml-2 text-xs text-text-muted">saved {formatDate(i.created_at)}</span>
              </span>
              <span className="flex items-center gap-3">
                <button type="button" onClick={() => void open(i.id, i.name)} className="text-accent-ink hover:underline">Open</button>
                <button type="button" onClick={() => void remove(i.id)} className="text-text-muted hover:text-text">Remove</button>
              </span>
            </li>
          ))}
        </ul>
      ) : items ? (
        <p className="mt-3 text-sm text-text-muted">Nothing saved yet. Set up a what-if above and save it to come back to it.</p>
      ) : null}
    </Blueprint>
  );
}
