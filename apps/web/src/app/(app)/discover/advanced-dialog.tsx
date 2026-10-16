"use client";

import { XIcon } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/** The advanced filters in a modal dialog: focus is trapped, Escape and the backdrop close it. */
export function AdvancedDialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby="advanced-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="m-auto max-h-[90dvh] w-[min(56rem,calc(100vw-1.5rem))] rounded-xl border border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-surface/70"
    >
      <div className="flex max-h-[90dvh] flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h2 id="advanced-title" className="font-heading text-xl leading-tight font-semibold">
            {title}
          </h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close advanced filters">
            <XIcon aria-hidden="true" />
          </Button>
        </div>
        <div className="space-y-4 overflow-y-auto px-5 py-4">{open ? children : null}</div>
      </div>
    </dialog>
  );
}
