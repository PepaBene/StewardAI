"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { refreshDiffbot, refreshEpo, refreshPdl, type ActionResult } from "@/lib/network/actions";

type Provider = {
  id: "diffbot" | "pdl" | "epo";
  label: string;
  cost: string;
  run: (ticker: string) => Promise<ActionResult>;
};

const PROVIDERS: readonly Provider[] = [
  {
    id: "diffbot",
    label: "Diffbot organisation match",
    cost: "Spends credits from the shared monthly free Diffbot pool. It suggests competitor, partner, acquisition, funding, leadership and parent candidates; none becomes a fact.",
    run: refreshDiffbot,
  },
  {
    id: "pdl",
    label: "People Data Labs company lookup",
    cost: "Spends one of about 100 free monthly lookups, only to fill an identity, headcount or funding gap. The server skips it inside a cooldown or when nothing is missing.",
    run: refreshPdl,
  },
  {
    id: "epo",
    label: "EPO OPS patent search",
    cost: "Uses the free EPO OPS request budget to search patents by the company's applicant aliases. Families stay unattributed until a dated ownership path exists.",
    run: refreshEpo,
  },
];

/**
 * Owner-triggered refreshes. Each spends free-tier quota, so a button only arms the action:
 * a second, explicit confirmation spends it. Nothing here runs on page load.
 */
export function RefreshActions({ ticker, onDone }: { ticker: string; onDone: () => void }) {
  const [armed, setArmed] = useState<Provider["id"] | null>(null);
  const [pending, setPending] = useState<Provider["id"] | null>(null);
  const [results, setResults] = useState<Partial<Record<Provider["id"], ActionResult>>>({});

  // Arming swaps the trigger for the confirm button (and back): focus follows so it is never dropped.
  const triggers = useRef<Partial<Record<Provider["id"], HTMLButtonElement | null>>>({});
  const confirms = useRef<Partial<Record<Provider["id"], HTMLButtonElement | null>>>({});
  const focusAfter = useRef<{ id: Provider["id"]; to: "confirm" | "trigger" } | null>(null);
  useEffect(() => {
    const f = focusAfter.current;
    if (!f) return;
    (f.to === "confirm" ? confirms : triggers).current[f.id]?.focus();
    focusAfter.current = null;
  }, [armed, pending]);
  function arm(id: Provider["id"] | null, back?: Provider["id"]) {
    focusAfter.current = id ? { id, to: "confirm" } : back ? { id: back, to: "trigger" } : null;
    setArmed(id);
  }

  async function confirm(p: Provider) {
    setPending(p.id);
    const r = await p.run(ticker);
    focusAfter.current = { id: p.id, to: "trigger" };
    setPending(null);
    setArmed(null);
    setResults((prev) => ({ ...prev, [p.id]: r }));
    if (r.ok) onDone();
  }

  return (
    <Blueprint as="section" aria-labelledby="network-refresh-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Free-quota actions" title={<span id="network-refresh-heading">Refresh from providers</span>} />
      <p className="max-w-prose text-sm text-text-muted">Each refresh spends a limited free allowance and only adds suggestions. Nothing here is bought, and nothing runs until you confirm.</p>
      <ul className="flex flex-col gap-3">
        {PROVIDERS.map((p) => (
          <li key={p.id} className="flex flex-col gap-1.5 border-t border-border-soft pt-3 first:border-0 first:pt-0">
            <p className="text-sm font-medium">{p.label}</p>
            <p className="max-w-prose text-xs text-text-muted">{p.cost}</p>
            <div className="flex flex-wrap items-center gap-2">
              {armed === p.id ? (
                <>
                  <Button ref={(el) => { confirms.current[p.id] = el; }} type="button" size="sm" disabled={pending !== null} onClick={() => void confirm(p)}>
                    {pending === p.id ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
                    Confirm: spend free quota
                  </Button>
                  <Button type="button" size="sm" variant="outline" disabled={pending !== null} onClick={() => arm(null, p.id)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <Button ref={(el) => { triggers.current[p.id] = el; }} type="button" size="sm" variant="outline" disabled={pending !== null} onClick={() => arm(p.id)}>
                  Refresh…
                </Button>
              )}
            </div>
            {results[p.id] ? (
              <p role={results[p.id]!.ok ? "status" : "alert"} className="text-xs text-text">
                {results[p.id]!.ok ? "" : "Not done. "}
                {results[p.id]!.message}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Blueprint>
  );
}
