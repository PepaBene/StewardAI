import { describeAtlasCell } from "@/lib/coverage/logic";
import { ATLAS_KINDS, type AtlasCell, type AtlasDrill, type AtlasRow } from "@/lib/coverage/types";
import { DATA_STATES, DATA_STATE_META, STATE_TONE_CLASSES } from "@/lib/data-state/states";
import { providerLabel } from "@/lib/source-coverage/providers";
import type { EntityKind } from "@/lib/source-coverage/types";
import { cn } from "@/lib/utils";

import { PatternDefs, PatternFill, STATE_ICONS } from "./state-glyph";

export const KIND_LABEL: Record<EntityKind, string> = {
  security: "Securities",
  geography: "Countries and regions",
  other: "Other entities",
};

const KIND_HINT: Record<EntityKind, string> = {
  security: "Entity key is a known ticker or listing",
  geography: "Entity key matches a macro or trade geography",
  other: "Funds, issuers, benchmarks, peers and unmatched keys; the API does not split these further",
};

function Cell({ cell, selected, onSelect }: { cell: AtlasCell; selected: boolean; onSelect: (c: AtlasCell) => void }) {
  if (cell.entries === 0 || cell.state === null) {
    return (
      <td className="border-l border-border-soft p-1 align-top">
        <div className="flex h-14 items-center justify-center border border-dashed border-border-soft px-2 text-center text-[11px] text-text-faint">No ledger entry (unknown)</div>
      </td>
    );
  }
  const meta = DATA_STATE_META[cell.state];
  const Icon = STATE_ICONS[cell.state];
  const present = DATA_STATES.filter((s) => (cell.byState[s] ?? 0) > 0);
  return (
    <td className="border-l border-border-soft p-1 align-top">
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`${cell.dataset}, ${KIND_LABEL[cell.kind]}: ${meta.label}. ${describeAtlasCell(cell)}. Show ledger rows.`}
        onClick={() => onSelect(cell)}
        className={cn(
          "relative flex h-14 w-full min-w-[7.5rem] flex-col items-start justify-between overflow-hidden border px-2 py-1 text-left outline-hidden focus-visible:outline-2 focus-visible:outline-focus",
          STATE_TONE_CLASSES[meta.tone].chip,
          selected && "ring-2 ring-accent",
        )}
      >
        <PatternFill state={cell.state} />
        <span className="relative flex items-center gap-1 text-xs font-semibold">
          <Icon aria-hidden="true" className="size-3.5" />
          {meta.label}
        </span>
        <span className="num relative bg-surface-raised/80 px-0.5 text-[11px] text-text">
          {cell.available}/{cell.entries} available
          {present.length > 1 ? ` · ${present.length} states` : ""}
        </span>
      </button>
    </td>
  );
}

/**
 * Dataset x entity-kind matrix. Each cell states its headline state with an icon, a fill pattern
 * and a label, plus "available/entries" counted from the ledger rows the API returned. An empty
 * cell means the ledger has no entry (unknown), not zero coverage.
 */
export function AtlasMatrix({ rows, drill, onDrill }: { rows: readonly AtlasRow[]; drill: AtlasDrill; onDrill: (d: AtlasDrill) => void }) {
  return (
    <div className="relative overflow-x-auto">
      <PatternDefs />
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <caption className="sr-only">Coverage ledger entries by dataset and entity kind</caption>
        <thead>
          <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-2 py-1.5 font-medium">
              Dataset
            </th>
            {ATLAS_KINDS.map((k) => (
              <th key={k} scope="col" className="border-l border-border-soft px-2 py-1.5 font-medium" title={KIND_HINT[k]}>
                {KIND_LABEL[k]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dataset} className="border-b border-border-soft last:border-0">
              <th scope="row" className="min-w-[8rem] px-2 py-1 text-left align-top font-normal">
                <span className="num block break-all text-text">{r.dataset}</span>
                <span className="block text-[11px] text-text-muted">{r.providers.map(providerLabel).join(", ")}</span>
              </th>
              {ATLAS_KINDS.map((k) => (
                <Cell
                  key={k}
                  cell={r.cells[k]}
                  selected={drill?.dataset === r.dataset && drill.kind === k}
                  onSelect={(c) => onDrill(drill?.dataset === c.dataset && drill.kind === c.kind ? null : { dataset: c.dataset, kind: c.kind })}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
