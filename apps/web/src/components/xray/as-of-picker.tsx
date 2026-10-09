"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { parseAsOf } from "@/lib/xray/as-of";

/** Pick the date the X-Ray shows. The choice lives in the address (`?as_of=`) so every tab reads
 * the same one. Not shown on What if, which always works on today's holdings. */
export function AsOfPicker() {
  const router = useRouter();
  const pathname = usePathname();
  const asOf = parseAsOf(useSearchParams().get("as_of"));
  const today = new Date().toISOString().slice(0, 10);

  function choose(value: string) {
    router.replace(parseAsOf(value) ? `${pathname}?as_of=${value}` : pathname);
  }

  return (
    <div className="flex flex-wrap items-end gap-3 py-3">
      <label className="text-sm">
        <span className="mb-1 block font-medium">Show the portfolio as it was on</span>
        <input
          type="date"
          value={asOf ?? ""}
          max={today}
          onChange={(e) => choose(e.target.value)}
          className="h-9 border border-border bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
        />
      </label>
      {asOf ? (
        <button type="button" onClick={() => choose("")} className="h-9 text-sm text-accent-ink underline underline-offset-2">
          Back to today
        </button>
      ) : (
        <span className="pb-2 text-xs text-text-muted">Today. Pick a date to see the holdings and fund contents known then.</span>
      )}
    </div>
  );
}
