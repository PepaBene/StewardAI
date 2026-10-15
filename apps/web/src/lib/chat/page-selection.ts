/**
 * What the owner has picked on the current page, as a short label for the chat ("company ASML",
 * "decision 12"). It is read from the address only, so it can never carry free text, and the
 * server drops anything that is not a plain label.
 */

const PATH_ITEMS: { prefix: string; label: string }[] = [
  { prefix: "/companies/", label: "company" },
  { prefix: "/theses/", label: "thesis" },
  { prefix: "/thesis/", label: "thesis" },
  { prefix: "/research/", label: "research report" },
  { prefix: "/reports/", label: "report" },
  { prefix: "/funds/", label: "fund" },
];

const QUERY_ITEMS: { param: string; label: string }[] = [
  { param: "decision", label: "decision" },
  { param: "ticker", label: "company" },
  { param: "notice", label: "notice" },
  { param: "change", label: "change" },
  { param: "version", label: "version" },
];

const PLAIN = /^[A-Za-z0-9_:#.-]{1,40}$/;

function clean(value: string | null | undefined): string | null {
  if (!value) return null;
  let decoded = value;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return null;
  }
  return PLAIN.test(decoded) ? decoded : null;
}

export function pageSelection(pathname: string, search: string): string | null {
  const query = new URLSearchParams(search);
  for (const { param, label } of QUERY_ITEMS) {
    const value = clean(query.get(param));
    if (value) return `${label} ${value}`;
  }
  for (const { prefix, label } of PATH_ITEMS) {
    if (!pathname.startsWith(prefix)) continue;
    const value = clean(pathname.slice(prefix.length).split("/")[0]);
    if (value) return `${label} ${value}`;
  }
  return null;
}
