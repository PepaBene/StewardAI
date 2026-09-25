"use client";

import { ArrowRightIcon, CommandIcon, LoaderCircleIcon, PlayIcon, SearchIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding } from "@/lib/api/client";
import {
  baseCommands,
  filterCommands,
  holdingCommands,
  type PaletteAction,
  type PaletteCommand,
} from "@/lib/palette";
import { cn } from "@/lib/utils";

function queue(action: PaletteAction) {
  const api = getBrowserApiClient();
  switch (action.kind) {
    case "earnings-check":
      return api.POST("/jobs/earnings-check", { body: { ticker: action.ticker } });
    case "portfolio-sync":
      return api.POST("/jobs/portfolio-sync");
    case "news-ingest":
      return api.POST("/jobs/news-ingest");
    case "digest":
      return api.POST("/jobs/digest", { body: { kind: action.period } });
    case "thesis-verdicts":
      return api.POST("/jobs/thesis-verdicts", { body: {} });
    case "reference-data":
      return api.POST("/jobs/reference-data", {
        // One holding: re-read OpenFIGI and Wikidata now. All: the weekly job's normal refresh.
        body: { ticker: action.ticker ?? null, force: Boolean(action.ticker) },
      });
    case "price-history":
      return api.POST("/jobs/price-history");
    case "holiday-refresh":
      return api.POST("/jobs/holiday-refresh");
  }
}

async function runAction(action: PaletteAction): Promise<string> {
  const result = await queue(action);
  if (!result.data) return `Could not queue it: the API answered HTTP ${result.response.status}.`;
  return `Queued as job #${result.data.id}.`;
}

/**
 * Cmd/Ctrl+K: jump to any section, config editor or holding, or re-run a check, without the
 * mouse. A native modal <dialog> keeps focus inside while open; Escape closes it.
 */
export function CommandPalette() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [status, setStatus] = useState<{ busy: boolean; text: string } | null>(null);

  const commands = useMemo(() => [...baseCommands(), ...holdingCommands(holdings)], [holdings]);
  const results = useMemo(() => filterCommands(commands, query), [commands, query]);
  const selected = Math.min(active, Math.max(0, results.length - 1));

  const open = useCallback(() => {
    setQuery("");
    setActive(0);
    setStatus(null);
    dialog.current?.showModal();
    input.current?.focus();
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (dialog.current?.open) dialog.current.close();
        else open();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function execute(command: PaletteCommand | undefined) {
    if (!command) return;
    if (command.href) {
      dialog.current?.close();
      router.push(command.href);
      return;
    }
    if (command.action) {
      setStatus({ busy: true, text: `${command.label}…` });
      try {
        setStatus({ busy: false, text: `${command.label}: ${await runAction(command.action)}` });
      } catch (err) {
        setStatus({ busy: false, text: err instanceof Error ? err.message : String(err) });
      }
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((selected + 1) % Math.max(1, results.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((selected - 1 + results.length) % Math.max(1, results.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      void execute(results[selected]);
    }
  }

  return (
    <>
      <Button variant="ghost" size="sm" onClick={open} aria-keyshortcuts="Control+K Meta+K">
        <SearchIcon aria-hidden="true" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden rounded-sm border border-border px-1 font-mono text-xs text-text-muted sm:inline-flex sm:items-center">
          <CommandIcon aria-hidden="true" className="size-3" />K
        </kbd>
      </Button>
      <dialog
        ref={dialog}
        aria-label="Command palette"
        className="m-auto mt-[15vh] w-[min(36rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-text/30"
        onClick={(e) => {
          if (e.target === dialog.current) dialog.current.close();
        }}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <SearchIcon aria-hidden="true" className="size-4 text-text-muted" />
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={results[selected] ? `${listId}-${results[selected].id}` : undefined}
            aria-label="Go to or run"
            placeholder="Go to a page, a holding, or run a check…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            className="h-12 flex-1 bg-transparent text-base outline-none placeholder:text-text-muted"
          />
        </div>
        <ul id={listId} role="listbox" aria-label="Commands" className="max-h-[50vh] overflow-y-auto p-1">
          {results.length === 0 ? <li className="px-3 py-6 text-center text-sm text-text-muted">No matches.</li> : null}
          {results.map((c, i) => (
            <li
              key={c.id}
              id={`${listId}-${c.id}`}
              role="option"
              aria-selected={i === selected}
              onMouseEnter={() => setActive(i)}
              onClick={() => void execute(c)}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm",
                i === selected ? "bg-surface-muted text-text" : "text-text-muted",
              )}
            >
              {c.action ? <PlayIcon aria-hidden="true" className="size-4" /> : <ArrowRightIcon aria-hidden="true" className="size-4" />}
              <span className="flex-1 text-text">{c.label}</span>
              <span className="text-xs text-text-muted">{c.group}</span>
            </li>
          ))}
        </ul>
        <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs text-text-muted" role="status">
          {status ? (
            <span className="flex items-center gap-1.5 text-text">
              {status.busy ? <LoaderCircleIcon aria-hidden="true" className="size-3.5 animate-spin" /> : null}
              {status.text}
            </span>
          ) : (
            <span>↑↓ to move · Enter to open or run · Esc to close</span>
          )}
        </div>
      </dialog>
    </>
  );
}
