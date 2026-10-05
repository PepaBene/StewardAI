"use client";

import { PinIcon } from "lucide-react";
import { useState } from "react";

import { Field, SaveNote, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { pinReasonError, pinRequest } from "@/lib/funds/forms";
import { apiErrorText } from "@/lib/reference/form-utils";

/**
 * Protect the holdings snapshots this look-through rests on from retention pruning
 * (POST /funds/snapshots/pin). It pins every configured fund's snapshot known at the date.
 */
export function PinSnapshot({ asOf, snapshotCount }: { asOf: string; snapshotCount: number }) {
  const [reason, setReason] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const error = pinReasonError(reason);

  async function pin() {
    if (error) return;
    setSave({ kind: "saving" });
    try {
      const r = await getBrowserApiClient().POST("/funds/snapshots/pin", { body: pinRequest(asOf, reason) });
      if (r.response.ok && r.data) {
        const n = r.data.pinned.length;
        setSave({ kind: "ok", text: n === 0 ? `Nothing to pin: no configured fund had a snapshot known on ${asOf}.` : `Pinned ${n} snapshot${n === 1 ? "" : "s"} known on ${asOf}; retention will keep them.` });
      } else setSave({ kind: "error", text: apiErrorText(r.error, r.response.status).replace("Not saved", "Not pinned") });
    } catch (err) {
      setSave({ kind: "error", text: `Not pinned: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-3 border-t border-border-soft pt-3">
      <Field label="Reason to keep these snapshots" hint="Optional, up to 500 characters." error={error ?? undefined} className="w-72 max-w-full">
        <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
      </Field>
      <Button type="button" variant="outline" size="sm" onClick={() => void pin()} disabled={save.kind === "saving" || error !== null}>
        <PinIcon aria-hidden="true" className="size-4" />
        Pin this snapshot
      </Button>
      <SaveNote
        state={save}
        idle={<span>Pins the {snapshotCount} snapshot{snapshotCount === 1 ? "" : "s"} used here so they are not pruned.</span>}
      />
    </div>
  );
}
