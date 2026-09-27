"use client";

import { MessageCircleQuestionIcon } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { openChatPanel } from "@/components/chat/chat-panel";

type Selected = { top: number; left: number; text: string };

/**
 * Wraps a region of figures or claims (a facts table, a KPI cell, a digest sentence):
 * selecting text inside it shows a small floating "Ask about this" button that opens the
 * chat panel seeded with a question about exactly that selection — Notion's
 * selection-triggered contextual menu, applied to "ask about this number" rather than a
 * global chat box as a second step.
 */
export function AskAboutSelection({ children, className }: { children: ReactNode; className?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<Selected | null>(null);

  function checkSelection() {
    const selection = window.getSelection();
    const text = selection?.toString().trim() ?? "";
    const container = containerRef.current;
    if (!text || !selection || selection.rangeCount === 0 || !container) {
      setSelected(null);
      return;
    }
    const range = selection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) {
      setSelected(null);
      return;
    }
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      setSelected(null);
      return;
    }
    setSelected({ top: rect.bottom + 6, left: rect.left, text });
  }

  useEffect(() => {
    if (!selected) return;
    function onPointerDown(e: MouseEvent) {
      if (!(e.target as HTMLElement).closest("[data-ask-about-selection-button]")) setSelected(null);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [selected]);

  return (
    <div ref={containerRef} className={className} onMouseUp={checkSelection} onKeyUp={checkSelection}>
      {children}
      {selected
        ? createPortal(
            <button
              type="button"
              data-ask-about-selection-button
              style={{ top: selected.top, left: selected.left }}
              className="fixed z-50 inline-flex items-center gap-1 rounded-md border border-border bg-surface-raised px-2 py-1 text-xs font-medium text-text shadow-lg hover:border-accent"
              onClick={() => {
                openChatPanel(`About "${selected.text}": `);
                window.getSelection()?.removeAllRanges();
                setSelected(null);
              }}
            >
              <MessageCircleQuestionIcon aria-hidden="true" className="size-3.5" />
              Ask about this
            </button>,
            document.body,
          )
        : null}
    </div>
  );
}
