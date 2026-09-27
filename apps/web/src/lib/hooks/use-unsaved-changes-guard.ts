"use client";

import { useEffect } from "react";

import { confirmLeave, LEAVE_PROMPT, leavesPage, setUnsavedChanges } from "@/lib/unsaved-changes";

/**
 * While `dirty`, ask before the edits are lost: on a tab close or reload (`beforeunload`),
 * and on a click on any in-app link (captured on the document, ahead of Next's <Link>
 * handler). Programmatic navigation (the command palette) checks `confirmLeave()` itself.
 */
export function useUnsavedChangesGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    const token = Symbol("unsaved-editor");
    setUnsavedChanges(token, true);

    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      // Older browsers show the prompt only when returnValue is set.
      event.returnValue = LEAVE_PROMPT;
    }
    function onClick(event: MouseEvent) {
      const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (!leavesPage(event, anchor, window.location)) return;
      if (confirmLeave()) return;
      event.preventDefault();
      event.stopPropagation();
    }

    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      setUnsavedChanges(token, false);
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty]);
}
