import { useEffect } from "react";

/**
 * The item a screen has picked in its own state (a selected row, an open panel), for screens
 * whose address does not say it. The chat reads it when a message is sent. Only a short plain
 * label is kept; the server checks it again.
 */
let current: string | null = null;

const PLAIN = /^[A-Za-z0-9 _:#.-]{1,60}$/;

export function setPageSelection(label: string | null): void {
  current = label && PLAIN.test(label) ? label : null;
}

export function getPageSelection(): string | null {
  return current;
}

/** Tells the chat what the screen has selected, and forgets it when the screen goes away. */
export function usePageSelection(label: string | null): void {
  useEffect(() => {
    setPageSelection(label);
    return () => setPageSelection(null);
  }, [label]);
}
