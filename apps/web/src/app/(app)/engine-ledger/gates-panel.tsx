"use client";

import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Gates = components["schemas"]["GateOut"];

const FIELDS: { key: keyof Gates; label: string; help: string; step: string }[] = [
  {
    key: "min_n",
    label: "Decisions needed",
    help: "A stricter floor is only judged once this many decisions can be replayed (5 to 1000).",
    step: "1",
  },
  {
    key: "min_reviewed",
    label: "Of them, checked",
    help: "And at least this many of them were checked by you or a second model (3 to 500).",
    step: "1",
  },
  {
    key: "max_wrong_rate_increase",
    label: "Wrong share must change by",
    help: "A fraction: -0.01 means one point fewer wrong decisions. Between -0.5 and 0.1.",
    step: "0.01",
  },
];

/** The bar a stricter confidence floor must clear. Only you can change it; the engine cannot. */
export function GatesPanel() {
  const [gates, setGates] = useState<Gates | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getBrowserApiClient()
      .GET("/engine/gates")
      .then((r) => {
        if (r.data) setGates(r.data);
        else setError(`Could not load the gates (HTTP ${r.response.status}).`);
      });
  }, []);

  async function save() {
    if (!gates) return;
    setMessage(null);
    setError(null);
    const r = await getBrowserApiClient().PUT("/engine/gates", { body: gates });
    if (r.data) {
      setGates(r.data);
      setMessage("Saved. New proposals use these numbers.");
    } else {
      setError(
        r.response.status === 422
          ? "One of the numbers is outside its allowed range."
          : `Not saved (HTTP ${r.response.status}).`,
      );
    }
  }

  return (
    <Blueprint>
      <div className="space-y-3 p-5">
        <h2 className="text-[15px] font-medium">What a stricter floor must prove first</h2>
        <p className="max-w-prose text-sm text-text-muted">
          Before the engine raises a confidence floor, it replays the past with and without it.
          These numbers are the bar. Only you can change them; the engine cannot propose a change
          to them.
        </p>
        {gates ? (
          <div className="grid max-w-2xl gap-3 sm:grid-cols-3">
            {FIELDS.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`gate-${f.key}`}>{f.label}</Label>
                <Input
                  id={`gate-${f.key}`}
                  type="number"
                  step={f.step}
                  value={gates[f.key]}
                  onChange={(e) => setGates({ ...gates, [f.key]: Number(e.target.value) })}
                />
                <p className="text-xs text-text-muted">{f.help}</p>
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex items-center gap-3">
          <Button size="sm" onClick={() => void save()} disabled={!gates}>
            Save the bar
          </Button>
          {message ? <span className="text-sm text-text-muted">{message}</span> : null}
        </div>
        {error ? <AlertMessage title="Not saved">{error}</AlertMessage> : null}
      </div>
    </Blueprint>
  );
}
