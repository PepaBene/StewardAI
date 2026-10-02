import { StackedBar } from "@/components/charts";
import { ChartFrame } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { EXPOSURE_KIND_LABELS, exposureSummary } from "@/lib/macro/logic";
import type { ExposureKind, HoldingExposureMap } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

/** Distinct borders so the three weight kinds differ by more than colour. */
const KIND_STYLE: Record<ExposureKind, string> = {
  reported: "border-solid border-text",
  estimated: "border-dashed border-text",
  user_assigned: "border-dotted border-text",
};

export function ExposureKindTag({ kind }: { kind: ExposureKind }) {
  return (
    <span
      data-kind={kind}
      className={cn("inline-flex items-center border-2 px-1 font-mono text-[10px] tracking-wide text-text uppercase", KIND_STYLE[kind])}
      title={
        kind === "reported"
          ? "Taken from the company's own disclosure"
          : kind === "estimated"
            ? "Allocated by a stated method; not disclosed"
            : "Your own assumption"
      }
    >
      {EXPOSURE_KIND_LABELS[kind]}
    </span>
  );
}

/**
 * Segment/product → country weights of one holding. Reported, estimated and owner-assigned
 * weights are separate and visibly different; whatever the weights do not explain is an
 * explicit "Unknown" remainder. Issuer impact on other screens is derived from these rows only.
 */
export function ExposurePanel({ map }: { map: HoldingExposureMap }) {
  const s = exposureSummary(map);
  const kindSegments = [
    { label: "Reported", value: s.byKind.reported },
    { label: "Estimated", value: s.byKind.estimated },
    { label: "Owner-assigned", value: s.byKind.user_assigned },
    { label: "Unknown", value: s.unknown },
  ];
  return (
    <ChartFrame
      id="exposure-map"
      kicker="Exposure mapping"
      title={`Where ${map.name} earns its revenue`}
      takeaway={`${formatPct(s.mapped, 0)} of ${map.period} revenue is mapped to a country (${formatPct(s.byKind.reported, 0)} reported); ${formatPct(s.unknown, 0)} is unknown.`}
      units={`% of total revenue, ${map.period}; denominator is the holding's total revenue`}
      provenance={{
        source: "Company disclosure, estimator and owner mapping",
        asOf: null,
        method: "Weights by country; kinds never merged; remainder shown as Unknown",
        state: s.overAllocated ? "disputed" : s.unknown > 0 ? "partial" : "verified",
        reason: s.overAllocated ? "Mapped weights exceed 100%: fix the mapping before using it." : null,
        coverage: { covered: Math.round(s.mapped * 100), total: 100, unit: "% of revenue" },
      }}
      table={{
        caption: `${map.name} revenue exposure by country and weight kind`,
        columns: ["Country", "Segment", "Weight", "Kind", "Evidence"],
        rows: [
          ...map.weights.map((w) => [w.targetName, w.segment, formatPct(w.weight, 1), EXPOSURE_KIND_LABELS[w.kind], w.evidence]),
          ["Unknown", "—", formatPct(s.unknown, 1), "—", "Not covered by any mapping"],
        ],
      }}
    >
      <StackedBar title="By kind of evidence" note={`Mapped ${formatPct(s.mapped, 0)}`} segments={kindSegments} />
      <ul className="m-0 grid list-none grid-cols-1 gap-x-8 gap-y-1.5 p-0 md:grid-cols-2">
        {s.byTarget.map((t) => (
          <li key={t.target} className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-2 text-sm">
            <span className="truncate">{t.targetName}</span>
            <span className="flex h-2 bg-surface-muted" aria-hidden="true">
              <span className="block h-full bg-accent" style={{ width: `${Math.min(100, t.weight * 100)}%` }} />
            </span>
            <span className="num text-right">{formatPct(t.weight, 0)}</span>
            <span className="col-span-3 -mt-0.5 flex flex-wrap gap-1">
              {t.kinds.map((k) => (
                <ExposureKindTag key={k} kind={k} />
              ))}
            </span>
          </li>
        ))}
        <li className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-2 text-sm">
          <span className="text-text-muted">Unknown</span>
          <span className="flex h-2 border border-dashed border-border" aria-hidden="true">
            <span className="block h-full bg-border" style={{ width: `${s.unknown * 100}%` }} />
          </span>
          <span className="num text-right text-text-muted">{formatPct(s.unknown, 0)}</span>
        </li>
      </ul>
    </ChartFrame>
  );
}
