"use client";

import { XIcon } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/**
 * AssumptionPanel (visual spec B41, page type T5): the scenario editors in a side panel titled
 * "Edit assumptions", with the live result pinned at the top. The children stay mounted while the
 * panel is closed, so an unsaved edit and the headline value survive closing and reopening.
 */
export function AssumptionPanel({
  open,
  onClose,
  result,
  children,
}: {
  open: boolean;
  onClose: () => void;
  /** The live result card pinned under the title: value, price gap, verdict. */
  result: ReactNode;
  children: ReactNode;
}) {
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
      aria-labelledby="assumption-panel-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className="fixed inset-y-0 right-0 m-0 ml-auto h-dvh max-h-dvh w-[min(36rem,100vw)] overflow-y-auto border-l border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-surface/80"
    >
      <div className="sticky top-0 z-10 space-y-3 border-b border-border bg-surface-raised px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <h2 id="assumption-panel-title" className="panel-title">
            Edit assumptions
          </h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close the assumptions panel">
            <XIcon aria-hidden="true" />
          </Button>
        </div>
        {result}
      </div>
      <div className="space-y-5 px-5 py-4">{children}</div>
    </dialog>
  );
}
