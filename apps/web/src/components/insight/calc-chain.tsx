import { num, type Num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F29 CalcChain: the calculation printed exactly as the model ran it, one ordered row per step
 * (label, expression, value with its unit). Nothing here recomputes; the rows are the backend's.
 */

export type CalcStepRow = { label: string; expression: string; value: Num; unit: string };

export function formatStep(value: Num, unit: string, currency?: string | null): string {
  const v = num(value);
  if (v === null) return "—";
  if (unit === "ratio" || unit === "percent" || unit === "fraction") return `${(v * 100).toFixed(1)}%`;
  if (unit === "multiple" || unit === "x") return `${v.toFixed(1)}×`;
  if (unit === "shares") return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v);
  const abs = Math.abs(v);
  const digits = abs >= 1e6 ? 0 : 2;
  const body = new Intl.NumberFormat(undefined, { notation: abs >= 1e9 ? "compact" : "standard", maximumFractionDigits: abs >= 1e9 ? 2 : digits }).format(v);
  // Money steps carry their currency code as the unit ("USD").
  if (/^[A-Z]{3}$/.test(unit)) return `${unit} ${body}`;
  if (unit === "currency" || unit === "per_share" || unit === "money") return `${currency ? `${currency} ` : ""}${body}`;
  return body;
}

export function CalcChain({ steps, currency, caption, className }: { steps: readonly CalcStepRow[]; currency?: string | null; caption: string; className?: string }) {
  return (
    <div className={cn("overflow-x-auto border border-border", className)}>
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="bg-surface-muted text-left text-xs text-text-muted">
            <th scope="col" className="w-8 px-3 py-1.5 font-medium">
              Step
            </th>
            <th scope="col" className="px-3 py-1.5 font-medium">
              What
            </th>
            <th scope="col" className="px-3 py-1.5 font-medium">
              How
            </th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">
              Result
            </th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={i} className={cn("border-t border-border-soft align-top", i === steps.length - 1 && "font-semibold")}>
              <td className="num px-3 py-1.5 text-text-faint">{i + 1}</td>
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {s.label}
              </th>
              <td className="px-3 py-1.5 font-mono text-xs break-words text-text-muted">{s.expression}</td>
              <td className="num px-3 py-1.5 text-right whitespace-nowrap">{formatStep(s.value, s.unit, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
