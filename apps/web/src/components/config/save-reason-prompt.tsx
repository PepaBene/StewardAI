"use client";

import { useEffect, useState } from "react";

import { ReasonChips, type OverrideReason } from "@/app/(app)/autopilot/reason-chips";
import { getBrowserApiClient } from "@/lib/api/client";
import { CONFIG_SAVED_EVENT } from "@/lib/api/config-saved";

/**
 * After you save a config the engine wrote, asks once why you changed it. It appears only when
 * the save overruled the engine, never blocks anything, and Skip or Cancel closes it.
 */
export function SaveReasonPrompt() {
  const [areas, setAreas] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    async function onSaved() {
      try {
        const { data } = await getBrowserApiClient().GET("/overrides/unexplained");
        setAreas(data ? Object.keys(data.areas) : []);
      } catch {
        setAreas([]);
      }
    }
    window.addEventListener(CONFIG_SAVED_EVENT, onSaved);
    return () => window.removeEventListener(CONFIG_SAVED_EVENT, onSaved);
  }, []);

  if (areas.length === 0) return null;

  async function pick(reason: OverrideReason | null) {
    if (reason !== null) {
      setBusy(true);
      try {
        for (const area of areas) {
          await getBrowserApiClient().POST("/overrides/explain", {
            body: { area, reason_code: reason },
          });
        }
      } finally {
        setBusy(false);
      }
    }
    setAreas([]);
  }

  return (
    <div
      role="status"
      className="fixed right-4 bottom-4 z-40 max-w-md rounded-lg border border-border bg-surface-raised p-3 shadow-lg"
    >
      <p className="text-sm font-medium">You changed something the engine wrote.</p>
      <ReasonChips disabled={busy} onPick={(r) => void pick(r)} onCancel={() => setAreas([])} />
    </div>
  );
}
