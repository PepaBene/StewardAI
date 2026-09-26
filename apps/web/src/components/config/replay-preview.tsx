"use client";

import { ArrowRightIcon, BellOffIcon, BellPlusIcon, BellRingIcon, LoaderCircleIcon, SaveIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import type { ReplayPreview } from "@/lib/api/client";
import {
  changeRows,
  PREVIEW_DOMAIN_LABELS,
  previewSummary,
  type ChangeField,
  type ChangeRow,
  type OutcomeView,
} from "@/lib/config/replay";
import { cn } from "@/lib/utils";

type Result<T> = { data?: T; error?: unknown; response: Response };

export type ReplayGate<B> =
  | { kind: "idle" }
  | { kind: "previewing" }
  | { kind: "ready"; preview: ReplayPreview; body: B; note: string | null }
  | { kind: "failed"; message: string; body: B; note: string | null }
  | { kind: "saving" };

/**
 * Save through a replay preview: Save first asks the API what the last 7 days would have
 * alerted under the draft, shows that diff, and only a second, explicit "Confirm and save"
 * stores the new version. An invalid draft (422) is handed to `onInvalid` (which shows the
 * field errors) and nothing is saved.
 */
export function useReplayGate<B>({
  preview,
  save,
  onInvalid,
}: {
  preview: (body: B) => Promise<Result<ReplayPreview>>;
  save: (body: B, note: string | null) => Promise<unknown>;
  onInvalid: (body: B) => void;
}) {
  const [gate, setGate] = useState<ReplayGate<B>>({ kind: "idle" });

  const request = useCallback(
    async (body: B, note: string | null) => {
      setGate({ kind: "previewing" });
      try {
        const result = await preview(body);
        if (result.data) setGate({ kind: "ready", preview: result.data, body, note });
        else if (result.response.status === 422) {
          setGate({ kind: "idle" });
          onInvalid(body);
        } else setGate({ kind: "failed", message: `The preview answered HTTP ${result.response.status}.`, body, note });
      } catch (err) {
        setGate({ kind: "failed", message: err instanceof Error ? err.message : String(err), body, note });
      }
    },
    [preview, onInvalid],
  );

  const confirm = useCallback(async () => {
    if (gate.kind !== "ready" && gate.kind !== "failed") return;
    const { body, note } = gate;
    setGate({ kind: "saving" });
    try {
      await save(body, note);
    } finally {
      setGate({ kind: "idle" });
    }
  }, [gate, save]);

  const cancel = useCallback(() => setGate({ kind: "idle" }), []);

  return { gate, request, confirm, cancel };
}

const FIELD_LABELS: Record<Exclude<ChangeField, "decision">, string> = {
  severity: "Severity",
  channels: "Channels",
  held_until: "Held until",
  suppressed: "Suppressed",
};

const EFFECT_ICONS = { new_alert: BellPlusIcon, no_longer_alerts: BellOffIcon, changed: BellRingIcon } as const;

/** The preview's diff in a modal dialog, with Confirm and save / Cancel. */
export function ReplayPreviewDialog<B>({
  gate,
  onConfirm,
  onCancel,
}: {
  gate: ReplayGate<B>;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const open = gate.kind === "ready" || gate.kind === "failed" || gate.kind === "saving";

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby="replay-title"
      onCancel={(e) => {
        e.preventDefault();
        if (gate.kind !== "saving") onCancel();
      }}
      className="m-auto max-h-[90dvh] w-[min(64rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-surface/80"
    >
      {open ? (
        <div className="space-y-4 p-5">
          <div>
            <h2 id="replay-title" className="text-lg font-semibold">
              Before you save: what this change would have done
            </h2>
            <p className="text-sm text-text-muted">
              The last 7 days of stored stories and alerts, routed under the saved rules and under your draft. Stored
              classifications only: no model or network call, nothing is saved until you confirm.
            </p>
          </div>

          {gate.kind === "failed" ? (
            <AlertMessage title="The preview could not be computed">
              <p>{gate.message}</p>
              <p className="mt-1">You can still save without seeing its effect, or cancel and try again.</p>
            </AlertMessage>
          ) : null}

          {gate.kind === "ready" ? <PreviewDiff preview={gate.preview} /> : null}

          <div className="sticky bottom-0 flex flex-wrap justify-end gap-2 border-t border-border bg-surface-raised pt-3">
            <Button variant="ghost" onClick={onCancel} disabled={gate.kind === "saving"}>
              Cancel: keep editing
            </Button>
            <Button onClick={onConfirm} disabled={gate.kind === "saving"}>
              {gate.kind === "saving" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SaveIcon aria-hidden="true" />}
              {gate.kind === "failed" ? "Save without a preview" : "Confirm and save"}
            </Button>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}

function PreviewDiff({ preview }: { preview: ReplayPreview }) {
  const rows = changeRows(preview);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Events replayed" value={String(preview.events)} />
        <Stat label="Alerts, saved rules" value={String(preview.alerts_before)} />
        <Stat label="Alerts, your draft" value={String(preview.alerts_after)} />
        <Stat label="Events that change" value={String(preview.changes.length)} />
      </dl>
      <p className="text-sm" role="status">
        {previewSummary(preview)}{" "}
        <span className="num text-text-muted">({preview.elapsed_ms} ms, {PREVIEW_DOMAIN_LABELS[preview.domain]} draft)</span>
      </p>
      {rows.length === 0 ? (
        <p className="rounded-md border border-dashed border-border bg-surface-muted p-3 text-sm">
          No event of the last 7 days would have been routed differently. The change may still matter for events to come.
        </p>
      ) : (
        <ol className="space-y-2" aria-label="Events whose outcome changes">
          {rows.map((row) => (
            <ChangeItem key={row.key} row={row} />
          ))}
        </ol>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border p-2">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num text-lg font-semibold">{value}</dd>
    </div>
  );
}

function ChangeItem({ row }: { row: ChangeRow }) {
  const Icon = EFFECT_ICONS[row.effect.kind];
  return (
    <li className="space-y-2 rounded-md border border-border p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium break-words">{row.headline}</p>
          <p className="flex flex-wrap gap-x-2 text-xs text-text-muted">
            {row.ticker ? <span className="font-mono">{row.ticker}</span> : null}
            <span>{row.event}</span>
            <span className="num">{row.at}</span>
          </p>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
            row.effect.kind === "changed" ? "border-border bg-neutral-signal-subtle" : "border-info-signal/40 bg-info-signal-subtle",
          )}
        >
          <Icon aria-hidden="true" className="size-3.5" />
          {row.effect.label}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_auto_1fr]">
        <Outcome title="Saved rules" outcome={row.before} changed={row.changed} />
        <ArrowRightIcon aria-hidden="true" className="hidden size-4 self-center text-text-muted sm:block" />
        <Outcome title="Your draft" outcome={row.after} changed={row.changed} emphasise />
      </div>
    </li>
  );
}

function Outcome({
  title,
  outcome,
  changed,
  emphasise = false,
}: {
  title: string;
  outcome: OutcomeView;
  changed: ChangeField[];
  emphasise?: boolean;
}) {
  const values: [Exclude<ChangeField, "decision">, string][] = [
    ["severity", outcome.severity],
    ["channels", outcome.channels],
    ["held_until", outcome.heldUntil ?? "—"],
    ["suppressed", outcome.suppressed ?? "—"],
  ];
  return (
    <div className={cn("rounded-md p-2", emphasise ? "bg-surface-muted" : "border border-dashed border-border")}>
      <p className="text-xs font-medium text-text-muted">{title}</p>
      <p className={cn("mt-0.5", changed.includes("decision") && emphasise && "font-medium")}>{outcome.decision}</p>
      <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-xs">
        {values.map(([field, value]) => {
          const differs = changed.includes(field);
          return (
            <div key={field} className="contents">
              <dt className="text-text-muted">{FIELD_LABELS[field]}</dt>
              <dd className={cn("num", differs && "font-semibold")}>
                {value}
                {differs ? <span className="ml-1 font-normal text-text-muted">(changed)</span> : null}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}
