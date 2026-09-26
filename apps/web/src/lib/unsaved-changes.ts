/**
 * App-wide registry of editors holding unsaved edits, so any navigation path (a link click,
 * the command palette, a tab close) can ask before throwing them away.
 */

export const LEAVE_PROMPT = "You have unsaved changes. Leave this page and lose them?";

const dirtyEditors = new Set<symbol>();

/** Mark one editor (by its token) as holding unsaved edits, or not. */
export function setUnsavedChanges(token: symbol, dirty: boolean): void {
  if (dirty) dirtyEditors.add(token);
  else dirtyEditors.delete(token);
}

export function hasUnsavedChanges(): boolean {
  return dirtyEditors.size > 0;
}

/**
 * True when navigating away is fine: nothing is unsaved, or the user confirmed losing it.
 * Call it before a programmatic `router.push`.
 */
export function confirmLeave(ask: (message: string) => boolean = (m) => window.confirm(m)): boolean {
  return !hasUnsavedChanges() || ask(LEAVE_PROMPT);
}

type ClickLike = {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
};

type AnchorLike = { href: string; target: string; hasAttribute: (name: string) => boolean };

/**
 * Whether a click on `anchor` would navigate this tab to another page of the app (the
 * navigation that unmounts an editor). New tabs, downloads, other origins and same-page
 * hash jumps keep the editor alive, so they pass.
 */
export function leavesPage(click: ClickLike, anchor: AnchorLike, current: { href: string }): boolean {
  if (click.defaultPrevented || click.button !== 0) return false;
  if (click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return false;
  if (anchor.target && anchor.target !== "_self") return false;
  if (anchor.hasAttribute("download") || !anchor.href) return false;
  let to: URL;
  let from: URL;
  try {
    to = new URL(anchor.href, current.href);
    from = new URL(current.href);
  } catch {
    return false;
  }
  if (to.origin !== from.origin) return false;
  return to.pathname !== from.pathname || to.search !== from.search;
}
