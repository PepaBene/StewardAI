"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { asOfParam, isIsoDate } from "@/lib/network/logic";

/**
 * The as-of day of the whole page, wired to the API `as_of` parameter (end of that day, UTC).
 * Empty means "now". Rows learned of, or decided, after the day are not applied.
 */
export function AsOfControl({ value, onChange, today }: { value: string; onChange: (v: string) => void; today: string }) {
  const sent = asOfParam(value);
  return (
    <div className="flex min-w-0 flex-wrap items-end gap-2" role="group" aria-label="As-of date">
      <div className="w-44 space-y-1">
        <Label htmlFor="network-as-of">As of (known by end of day)</Label>
        <Input
          id="network-as-of"
          type="date"
          value={value}
          max={today}
          onChange={(e) => onChange(isIsoDate(e.target.value) || e.target.value === "" ? e.target.value : value)}
          aria-describedby="network-as-of-hint"
        />
      </div>
      <Button type="button" variant="outline" size="sm" onClick={() => onChange("")} disabled={value === ""} className="mb-0.5">
        Now
      </Button>
      <p id="network-as-of-hint" className="num mb-1 text-xs text-text-muted">
        {sent ? `Sending as_of=${sent}. Statuses and rows are as known then.` : "Current view: everything known now."}
      </p>
    </div>
  );
}
