import Link from "next/link";

import { StatePlaceholder } from "@/components/data-state";
import { ExposureTag } from "@/components/macro/exposure-tag";
import { formatPct } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { toNumber } from "@/lib/holding-data/logic";
import type { TradeExposure } from "@/lib/holding-data/types";
import { geographyName, mappedTargets } from "@/lib/macro/logic";
import type { HoldingMacroContext } from "@/lib/macro/types";

import { day, HoldingPanel, SourceLink, TableWrap, TD, TH } from "./shared";

/**
 * Country and trade exposure of the holding with its macro context. The mapped targets, their
 * basis (reported / estimated / user-assigned) and the unmapped remainder come from the macro
 * store; indicators are country context, not a company-specific sensitivity. Full charts and the
 * mapping editor stay on /macro.
 */
export function ExposureCard({ macro, trade, ticker }: { macro: HoldingMacroContext | null; trade: readonly TradeExposure[]; ticker: string }) {
  const targets = macro ? mappedTargets(macro.effects) : [];
  const unknown = macro && macro.effects.length > 0 ? Math.max(...macro.effects.map((e) => e.unknownWeight ?? 0)) : null;
  const unmapped = !macro || macro.exposureUnknown || macro.effects.length === 0;
  const latestRelease = macro ? macro.observations.map((o) => o.knownAt).sort().at(-1) ?? null : null;
  return (
    <HoldingPanel
      id="exposure"
      kicker="Country and trade exposure"
      title="Where this holding is exposed"
      takeaway={
        unmapped
          ? "No country or product exposure mapping is known for this holding at this cutoff, so none is attributed to it."
          : `${targets.length} mapped target${targets.length === 1 ? "" : "s"}${unknown !== null && unknown > 0 ? `; up to ${formatPct(unknown, 0)} of the issuer in a dimension is unmapped and stays unknown` : ""}. ${macro?.observations.length ?? 0} country indicator${macro?.observations.length === 1 ? "" : "s"} give dated context.`
      }
      action={
        <Link href="/macro" className="text-xs text-accent-ink hover:underline">
          Open macro
        </Link>
      }
      provenance={{
        source: "Exposure mappings, stored country indicators and stored trade flows",
        asOf: macro ? macro.asOf : null,
        knownAt: latestRelease,
        method: "Mapped weights as stored (reported, estimated or user-assigned); the unmapped share is shown, not distributed.",
        state: !macro ? "unavailable" : unmapped ? "not_covered" : macro.observations.length === 0 ? "partial" : "verified",
        reason: !macro ? "The macro context could not be read." : unmapped ? "No exposure mapping was known by the cutoff." : macro.observations.length === 0 ? "Exposure is mapped but no country indicator was known by the cutoff." : null,
        coverage: macro && !unmapped ? { covered: macro.observations.length, total: Math.max(macro.observations.length, macro.coverage.length), unit: "indicator series" } : null,
      }}
    >
      {!macro ? (
        <StatePlaceholder state="unavailable" reason="The macro context of this holding is not available." minHeight={80} />
      ) : unmapped ? (
        <StatePlaceholder state="not_covered" reason="Add an exposure mapping on the macro page to link this holding to a country." minHeight={80} />
      ) : (
        <TableWrap caption={`Mapped exposure of ${ticker}`}>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border">
              <th scope="col" className={TH}>Segment</th>
              <th scope="col" className={TH}>Target</th>
              <th scope="col" className={TH}>Basis</th>
              <th scope="col" className={`${TH} text-right`}>Weight</th>
              <th scope="col" className={`${TH} text-right`}>Unmapped in dimension</th>
            </tr>
          </thead>
          <tbody>
            {macro.effects.map((e, i) => (
              <tr key={i} className="border-b border-border-soft last:border-0">
                <th scope="row" className={`${TD} text-left font-normal`}>{e.segment}</th>
                <td className={TD}>{e.geography ? `${geographyName(e.geography)} (${e.geography})` : e.hsCode ? `HS ${e.hsCode}` : "not reported"}</td>
                <td className={TD}><ExposureTag basis={e.basis} /></td>
                <td className={`${TD} num text-right`}>{isFiniteNumber(e.weight) ? formatPct(e.weight, 1) : "not reported"}</td>
                <td className={`${TD} num text-right`}>{isFiniteNumber(e.unknownWeight) ? formatPct(e.unknownWeight, 1) : "not reported"}</td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
      <h3 className="kicker mt-1">Trade exposure</h3>
      {trade.length === 0 ? (
        <p className="text-sm text-text-muted">No product (HS) exposure is mapped, so no trade flows are attributed to this holding.</p>
      ) : (
        <TableWrap caption={`Trade flows behind the product exposure of ${ticker}`}>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border">
              <th scope="col" className={TH}>Product</th>
              <th scope="col" className={TH}>Reporter</th>
              <th scope="col" className={`${TH} text-right`}>Weight</th>
              <th scope="col" className={TH}>Latest period</th>
              <th scope="col" className={TH}>Source</th>
            </tr>
          </thead>
          <tbody>
            {trade.map((t) => {
              const w = toNumber(t.exposure_weight);
              const row = t.rows[0];
              return (
                <tr key={`${t.hs_code}-${t.geography ?? ""}`} className="border-b border-border-soft align-top last:border-0">
                  <th scope="row" className={`${TD} text-left font-normal`}>HS {t.hs_code}{t.hs_revision ? ` (${t.hs_revision})` : ""}</th>
                  <td className={TD}>{t.geography ?? "not reported"}</td>
                  <td className={`${TD} num text-right`}>{w === null ? "not reported" : formatPct(w, 1)} <span className="text-text-muted">{t.exposure_basis}</span></td>
                  <td className={`${TD} num`}>{day(t.latest_period)}</td>
                  <td className={TD}>
                    {t.gap ? <span className="text-text-muted">No trade data: {t.gap}</span> : row ? <SourceLink url={row.source_url} label={`Trade flow source · known ${day(row.known_at)}`} /> : "no rows"}
                    <span className="mt-0.5 block text-text-muted">{t.notice}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </TableWrap>
      )}
    </HoldingPanel>
  );
}
