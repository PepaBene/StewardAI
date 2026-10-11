"use client";

import { Button } from "@/components/ui/button";
import { REPORT_VIEWS, type ReportView } from "@/lib/research/view";

/** Concise or detailed view of the one stored report; the pressed button is the view shown. */
export function ViewToggle({ view, onChange }: { view: ReportView; onChange: (view: ReportView) => void }) {
  return (
    <div role="group" aria-label="Report view" className="flex gap-1" data-print-hide>
      {REPORT_VIEWS.map((v) => (
        <Button
          key={v.id}
          size="sm"
          variant={view === v.id ? "default" : "outline"}
          aria-pressed={view === v.id}
          title={v.description}
          onClick={() => onChange(v.id)}
        >
          {v.label}
        </Button>
      ))}
    </div>
  );
}
