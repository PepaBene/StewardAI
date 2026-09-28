import { CATEGORICAL_BG, Donut } from "@/components/charts";
import { AllocationBars, type AllocationItem } from "@/components/portfolio/allocation-bars";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatMoney, formatPercent } from "@/lib/format";
import { formatCompactMoney } from "@/lib/portfolio/money";
import { cn } from "@/lib/utils";

const HEAD = 6;

type Slice = { key: string; label: string; title: string; value: number };

/** Largest positions, the rest folded into "Other N", cash last (8 categorical slots at most). */
function slices(items: AllocationItem[]): Slice[] {
  const positions = items.filter((i) => !i.muted).sort((a, b) => b.value - a.value);
  const cash = items.filter((i) => i.muted);
  const head = positions.length <= HEAD + 1 ? positions : positions.slice(0, HEAD);
  const tail = positions.slice(head.length);
  const out: Slice[] = head.map((i) => ({ key: i.key, label: i.key, title: i.label, value: i.value }));
  if (tail.length) {
    out.push({
      key: "__other",
      label: `Other ${tail.length}`,
      title: tail.map((t) => t.key).join(", "),
      value: tail.reduce((s, t) => s + t.value, 0),
    });
  }
  for (const c of cash) out.push({ key: c.key, label: c.label, title: c.label, value: c.value });
  return out;
}

/**
 * Allocation by holding: a donut with the position count in the hole and a legend with each
 * slice's share and amount; every position (with its full amount) is one click away.
 */
export function AllocationPanel({
  items,
  currency,
  fxSource,
}: {
  items: AllocationItem[];
  currency: string;
  fxSource: "cnb" | "ecb";
}) {
  const parts = slices(items);
  const total = parts.reduce((s, p) => s + p.value, 0);
  const positions = items.filter((i) => !i.muted).length;
  return (
    <Blueprint className="flex min-w-0 flex-col gap-4 px-6 py-5.5" aria-labelledby="allocation-heading">
      <PanelHeading kicker="Allocation" title={<span id="allocation-heading">By holding</span>} />
      <div className="flex flex-col items-center gap-5 sm:flex-row">
        <Donut
          segments={parts.map((p) => ({ label: p.label, value: p.value }))}
          centerValue={positions}
          centerLabel={positions === 1 ? "position" : "positions"}
          legend={false}
          label="Allocation by holding"
          className="shrink-0"
        />
        <ul className="flex w-full min-w-0 flex-1 flex-col gap-1.5 text-xs">
          {parts.map((p, i) => (
            <li
              key={p.key}
              className="grid grid-cols-[10px_minmax(0,1fr)_auto_auto] items-center gap-2"
              title={`${p.title}: ${formatMoney(p.value, currency, { decimals: 0 })}`}
            >
              <span aria-hidden="true" className={cn("size-2.5", CATEGORICAL_BG[i])} />
              <span className={cn("truncate text-text", p.key !== "CASH" && !p.key.startsWith("__") && "font-mono text-[11px]")}>{p.label}</span>
              <span className="num text-text-faint">{formatCompactMoney(p.value, currency)}</span>
              <span className="num w-11 text-right text-text-muted">{formatPercent(total > 0 ? p.value / total : null)}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-text-muted">
        In {currency} at {fxSource === "cnb" ? "ČNB" : "ECB"} reference rates.
      </p>
      <details className="group text-sm">
        <summary className="cursor-pointer text-xs font-medium text-accent-ink hover:underline">All positions with amounts</summary>
        <div className="mt-3">
          <AllocationBars items={items} currency={currency} label="Allocation by holding" />
        </div>
      </details>
    </Blueprint>
  );
}
