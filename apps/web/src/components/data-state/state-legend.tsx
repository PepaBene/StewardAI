import { DataStateBadge } from "@/components/data-state/data-state-badge";
import { DATA_STATES, DATA_STATE_META } from "@/lib/data-state/states";

/** The nine availability states with their meaning: the key to every badge in the app's data views. */
export function StateLegend({ counts }: { counts?: Partial<Record<(typeof DATA_STATES)[number], number>> }) {
  return (
    <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
      {DATA_STATES.map((s) => (
        <div key={s} className="flex flex-col gap-0.5">
          <dt className="flex items-center gap-2">
            <DataStateBadge state={s} />
            {counts ? <span className="num text-text-muted">{counts[s] ?? 0}</span> : null}
          </dt>
          <dd className="m-0 text-text-muted">{DATA_STATE_META[s].description}</dd>
        </div>
      ))}
    </dl>
  );
}
