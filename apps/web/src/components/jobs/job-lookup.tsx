"use client";

import { ArrowRightIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { jobHref } from "@/lib/jobs/job-detail";

/**
 * Open one job run by its number. There is no job list yet (the Activity page, phase 4);
 * job numbers are shown wherever a run is queued or reported.
 */
export function JobLookup() {
  const router = useRouter();
  const id = useId();
  const [value, setValue] = useState("");
  const number = Number(value.trim().replace(/^#/, ""));
  const valid = Number.isSafeInteger(number) && number > 0;

  return (
    <section aria-labelledby={`${id}-heading`} className="max-w-xl space-y-2">
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        Job runs
      </h2>
      <p className="text-sm text-text-muted">
        Open a run to see its steps, errors, detail and model cost. Its number is shown when you start a check, next to the
        last news check and on each holding&rsquo;s last earnings check and verdict run.
      </p>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) router.push(jobHref(number));
        }}
      >
        <div className="space-y-1">
          <Label htmlFor={id}>Job number</Label>
          <Input id={id} inputMode="numeric" className="w-40" placeholder="e.g. 42" value={value} onChange={(e) => setValue(e.target.value)} />
        </div>
        <Button type="submit" variant="outline" disabled={!valid}>
          <ArrowRightIcon aria-hidden="true" />
          Open
        </Button>
      </form>
    </section>
  );
}
