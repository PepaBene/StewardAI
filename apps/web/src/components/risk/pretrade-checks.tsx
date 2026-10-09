import { sortChecks, type PretradeCheck, type PretradeResult } from "@/lib/risk/risk";
import { cn } from "@/lib/utils";

const STATUS: Record<PretradeCheck["status"], { label: string; cls: string }> = {
  flag: { label: "Flag", cls: "border-warning-signal text-text" },
  pass: { label: "Pass", cls: "border-border text-text-muted" },
  unavailable: { label: "Cannot check", cls: "border-dashed border-border text-text-muted" },
  info: { label: "Note", cls: "border-border-soft text-text-muted" },
};

/** The checks of a planned trade, flags first. The words carry the status; the border only echoes it. */
export function PretradeChecks({ result }: { result: PretradeResult }) {
  const checks = sortChecks(result.checks);
  return (
    <div className="space-y-3">
      <p className="text-sm">
        <span className="font-semibold">{result.flagged}</span> flagged, <span className="font-semibold">{result.passed}</span> passed,{" "}
        <span className="font-semibold">{result.unavailable}</span> that could not be checked.
      </p>
      <ul className="space-y-2">
        {checks.map((c) => (
          <li key={`${c.id}-${c.ticker ?? ""}`} className={cn("border-l-2 py-1 pl-3", STATUS[c.status].cls)}>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-xs font-semibold">{STATUS[c.status].label}</span>
              <span className="text-sm font-medium">{c.title}</span>
              {c.ticker ? <span className="text-xs text-text-muted">{c.ticker}</span> : null}
              <span className="text-xs text-text-muted">{c.principle}</span>
            </div>
            <p className="max-w-prose text-sm text-text-muted">{c.detail}</p>
          </li>
        ))}
      </ul>
      {result.notes.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs text-text-muted">
          {result.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-text-muted">{result.disclaimer}</p>
    </div>
  );
}
