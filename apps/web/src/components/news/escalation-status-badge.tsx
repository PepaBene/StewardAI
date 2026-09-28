"use client";

import { BotIcon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EscalationBody, type EscalationOut } from "@/lib/api/client";
import {
  ESCALATION_STATE_LABELS,
  escalationActions,
  escalationStatus,
  type EscalationState,
} from "@/lib/news/escalation";
import { cn } from "@/lib/utils";

/** Where the owner is with an escalation: a filled chip for open, a pale one acknowledged, grey resolved. */
export function EscalationStateBadge({ status, className }: { status: EscalationState; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap",
        status === "open" && "bg-accent text-accent-foreground",
        status === "acknowledged" && "bg-accent-soft text-accent-ink",
        status === "resolved" && "bg-surface-muted text-text-muted",
        className,
      )}
    >
      {ESCALATION_STATE_LABELS[status]}
    </span>
  );
}

/**
 * Acknowledge / Resolve / Reopen: `PATCH /escalations/{id}`; hands the updated escalation
 * back. Errors are shown beside the buttons.
 */
export function EscalationActions({
  escalation,
  onUpdated,
  size = "sm",
  className,
}: {
  escalation: Pick<EscalationOut, "id" | "status" | "title">;
  onUpdated: (updated: EscalationOut) => void;
  size?: "sm" | "default";
  className?: string;
}) {
  const [pending, setPending] = useState<EscalationState | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function move(to: EscalationState) {
    setPending(to);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().PATCH("/escalations/{report_id}", {
        params: { path: { report_id: escalation.id } },
        body: { status: to },
      });
      if (data) onUpdated(data);
      else setError(`HTTP ${response.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1.5", className)}>
      {escalationActions(escalation.status).map((a, i) => (
        <Button
          key={a.to}
          type="button"
          size={size}
          variant={i === 0 && a.to !== "open" ? "outline" : "ghost"}
          className={size === "sm" ? "h-7 px-2 text-[13px]" : undefined}
          disabled={pending !== null}
          aria-label={`${a.label} escalation E-${escalation.id}: ${escalation.title}`}
          onClick={() => void move(a.to)}
        >
          {pending === a.to ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
          {a.label}
        </Button>
      ))}
      {error ? (
        <span role="alert" className="text-xs text-text">
          Not saved: {error}
        </span>
      ) : null}
    </span>
  );
}

/** Whether a model assessed an impact note (muted palette, an icon and a word). */
export function EscalationStatusBadge({ body, className }: { body: EscalationBody; className?: string }) {
  const status = escalationStatus(body);
  const Icon = status.kind === "assessed" ? BotIcon : CircleDashedIcon;
  return (
    <span
      title={status.kind === "not_assessed" ? `Not assessed: ${status.reason}` : status.label}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text",
        status.kind === "assessed" ? "border-info-signal/40 bg-info-signal-subtle" : "border-warning-signal/40 bg-warning-signal-subtle",
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn("size-3.5", status.kind === "assessed" ? "text-info-signal" : "text-warning-signal")} />
      {status.kind === "assessed" ? "Assessed" : "Not assessed"}
    </span>
  );
}
