"use client";

import { ExternalLinkIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/*
 * F27 SideDrawer: a right-hand panel opened from a table row, with tabs and a "Full page" link.
 * Built on <dialog> so focus is trapped and Escape closes it; below md it fills the screen as a
 * sheet.
 */

export type DrawerTab = { key: string; label: string; content: ReactNode };

export function SideDrawer({
  open,
  onClose,
  title,
  subtitle,
  fullPageHref,
  tabs,
  tab,
  onTab,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  fullPageHref?: string;
  tabs: readonly DrawerTab[];
  tab: string;
  onTab: (key: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const current = tabs.find((t) => t.key === tab) ?? tabs[0];
  return (
    <dialog
      ref={ref}
      aria-labelledby="drawer-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-full border-l border-border bg-surface-raised p-0 text-text shadow-lg backdrop:bg-surface/70 md:w-[min(34rem,90vw)]"
    >
      {open ? (
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between gap-3 border-b border-border px-5 pt-4 pb-3">
            <div className="min-w-0">
              <h2 id="drawer-title" className="truncate text-[22px] leading-tight font-semibold">
                {title}
              </h2>
              {subtitle ? <div className="mt-0.5 text-sm text-text-muted">{subtitle}</div> : null}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {fullPageHref ? (
                <Button asChild variant="outline" size="sm">
                  <Link href={fullPageHref}>
                    <ExternalLinkIcon aria-hidden="true" />
                    Full page
                  </Link>
                </Button>
              ) : null}
              <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
                <XIcon aria-hidden="true" />
              </Button>
            </div>
          </div>
          <div role="tablist" aria-label="Sections" className="flex gap-1 overflow-x-auto border-b border-border px-5">
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`drawer-tab-${t.key}`}
                aria-selected={t.key === current?.key}
                aria-controls="drawer-panel"
                onClick={() => onTab(t.key)}
                className={cn(
                  "-mb-px border-b-2 px-2.5 py-2 text-sm whitespace-nowrap",
                  t.key === current?.key ? "border-accent font-medium text-text" : "border-transparent text-text-muted hover:text-text",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div id="drawer-panel" role="tabpanel" aria-labelledby={`drawer-tab-${current?.key}`} className="flex-1 overflow-y-auto px-5 py-4">
            {current?.content}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
