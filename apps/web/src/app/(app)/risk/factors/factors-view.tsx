"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";
import {
  betaOf,
  betaScale,
  divergingBar,
  isCurrency,
  isSignificant,
  type FactorExposures,
  type PositionFactors,
  type Tilt,
} from "@/lib/risk/factors";
import { cn } from "@/lib/utils";

import { Notes, Warnings } from "../../xray/shared";
import { IdeaPanel } from "./idea-panel";
import { ShockPanel } from "./shock-panel";

type Region = "developed" | "europe" | "us";

const REGIONS: { id: Region; label: string }[] = [
  { id: "developed", label: "Developed markets" },
  { id: "europe", label: "Europe" },
  { id: "us", label: "United States" },
];

const fetchers: Record<Region, () => Promise<ApiResult<FactorExposures>>> = {
  developed: () => getBrowserApiClient().GET("/portfolio/risk/factors", { params: { query: { region: "developed" } } }),
  europe: () => getBrowserApiClient().GET("/portfolio/risk/factors", { params: { query: { region: "europe" } } }),
  us: () => getBrowserApiClient().GET("/portfolio/risk/factors", { params: { query: { region: "us" } } }),
};

export function FactorsView() {
  const [region, setRegion] = useState<Region>("developed");
  return (
    <div className="flex flex-col gap-5">
      <div role="group" aria-label="Factor region" className="flex flex-wrap gap-1.5">
        {REGIONS.map((r) => (
          <button
            key={r.id}
            type="button"
            aria-pressed={region === r.id}
            onClick={() => setRegion(r.id)}
            className={cn(
              "h-9 border px-3 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              region === r.id ? "border-accent bg-accent/10 font-semibold" : "border-border text-text-muted hover:text-text",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>
      <RegionReport key={region} region={region} />
      <ShockPanel region={region} />
      <IdeaPanel region={region} />
    </div>
  );
}

function RegionReport({ region }: { region: Region }) {
  const { state } = useApiResource(fetchers[region]);
  return (
    <ResourceGate state={state} label="factor exposures">
      {(report: FactorExposures) => <Report report={report} />}
    </ResourceGate>
  );
}

function Report({ report }: { report: FactorExposures }) {
  if (report.portfolio === null || report.portfolio === undefined) {
    return (
      <div className="flex flex-col gap-5">
        <Warnings warnings={report.warnings} />
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Factor exposures" title="No exposures yet" />
          <p className="mt-2 max-w-prose text-sm text-text-muted">{report.reason}</p>
        </Blueprint>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <Warnings warnings={report.warnings} />
      <Tilts report={report} />
      <Holdings report={report} />
      <Notes
        title="How this is calculated"
        notes={[
          report.method,
          `Window ${formatDate(report.window_start ?? "")} to ${formatDate(report.window_end ?? "")}, ${report.coverage?.window_weeks ?? 0} weekly returns. Factor data runs ${formatDate(report.coverage?.first_date ?? "")} to ${formatDate(report.coverage?.last_date ?? "")}. Method ${report.method_version}.`,
          "A loading is the change in a weekly return for a 1.0 move in the factor. Loadings with a t-statistic under 2 are drawn faint: they are within noise.",
        ]}
      />
    </div>
  );
}

/** Portfolio against benchmark on one centred scale, one row per factor. */
function Tilts({ report }: { report: FactorExposures }) {
  const scale = betaScale(report.tilts.flatMap((t) => [t.portfolio, t.benchmark]));
  const notes = report.tilts.filter((t) => t.flagged);
  const style = report.tilts.filter((t) => !isCurrency(t.factor));
  const currency = report.tilts.filter((t) => isCurrency(t.factor));
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Factor exposures" title="What your portfolio leans on, against the benchmark" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        The filled bar is your portfolio, the outline is {report.benchmark_label ?? "the benchmark"}. A bar to the right means the portfolio rises with
        that factor, to the left that it moves against it. R² {report.portfolio ? report.portfolio.r_squared.toFixed(2) : "—"}: how much of the weekly
        movement these factors explain.
      </p>
      {notes.length > 0 ? (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
          {notes.map((t) => (
            <li key={t.factor}>{t.note}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-text-muted">No style tilt is beyond {report.tilt_flag.toFixed(1)} against the benchmark.</p>
      )}
      <ul className="mt-3">
        {style.map((t) => (
          <TiltRow key={t.factor} tilt={t} scale={scale} report={report} flag={report.tilt_flag} />
        ))}
      </ul>
      {currency.length > 0 ? (
        <>
          <h4 className="mt-4 text-xs font-medium text-text-muted">Currencies and rates, against the base currency</h4>
          <ul className="mt-1">
            {currency.map((t) => (
              <TiltRow key={t.factor} tilt={t} scale={scale} report={report} flag={report.tilt_flag} />
            ))}
          </ul>
        </>
      ) : null}
    </Blueprint>
  );
}

function TiltRow({ tilt: t, scale, report, flag }: { tilt: Tilt; scale: number; report: FactorExposures; flag: number }) {
  const mine = betaOf(report.portfolio, t.factor);
  const bar = divergingBar(t.portfolio, scale);
  const ref = divergingBar(t.benchmark, scale);
  return (
    <li className="grid grid-cols-[minmax(0,8rem)_1fr_10rem] items-center gap-x-4 gap-y-1 border-b border-border-soft py-2 last:border-b-0 max-md:grid-cols-[1fr_auto]">
      <span className="text-sm font-medium">{t.label}</span>
      <div aria-hidden="true" className="relative h-4 bg-border-soft max-md:col-span-2 max-md:row-start-2">
        <div className="absolute inset-y-0 left-1/2 w-px bg-text-muted/50" />
        {t.benchmark !== null && t.benchmark !== undefined ? (
          <div className="absolute inset-y-0 border border-text-muted" style={{ left: `${ref.left}%`, width: `${ref.width}%` }} />
        ) : null}
        <div
          className={cn("absolute top-1 bottom-1", t.flagged ? "bg-warning-signal" : "bg-accent", !isSignificant(mine?.t_stat) && "opacity-40")}
          style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
        />
      </div>
      <dl className="num grid grid-cols-[auto_auto] justify-end gap-x-3 text-right text-sm">
        <dt className="text-text-muted">portfolio</dt>
        <dd className={cn("font-semibold", !isSignificant(mine?.t_stat) && "text-text-muted")}>{t.portfolio.toFixed(2)}</dd>
        <dt className="text-text-muted">benchmark</dt>
        <dd>{t.benchmark === null || t.benchmark === undefined ? "—" : t.benchmark.toFixed(2)}</dd>
        {t.tilt !== null && t.tilt !== undefined ? (
          <>
            <dt className="text-text-muted">tilt</dt>
            <dd className={cn(t.flagged && "font-semibold text-warning-signal")} title={`Flagged beyond ±${flag.toFixed(1)}`}>
              {t.tilt > 0 ? "+" : ""}
              {t.tilt.toFixed(2)}
            </dd>
          </>
        ) : null}
      </dl>
    </li>
  );
}

function Holdings({ report }: { report: FactorExposures }) {
  const factors = report.portfolio?.betas.map((b) => ({ id: b.factor, label: b.label })) ?? [];
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="By holding" title="Loadings of each holding" />
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Factor loadings of each holding; faint figures are within noise</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Size</th>
              {factors.map((f) => (
                <th key={f.id} scope="col" className="num py-1.5 pr-3 text-right font-medium">{f.label}</th>
              ))}
              <th scope="col" className="num py-1.5 text-right font-medium">R²</th>
            </tr>
          </thead>
          <tbody>
            {report.positions.map((p) => (
              <HoldingRow key={p.ticker} p={p} factors={factors} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-text-muted">{formatPercent(report.positions.reduce((s, p) => s + (p.fit ? p.weight : 0), 0), { decimals: 0 })} of the portfolio has a fitted line of its own.</p>
    </Blueprint>
  );
}

function HoldingRow({ p, factors }: { p: PositionFactors; factors: { id: string; label: string }[] }) {
  return (
    <tr className="border-b border-border-soft last:border-b-0">
      <th scope="row" className="py-1.5 pr-3 text-left">
        <span className="font-semibold">{p.ticker}</span>
        {p.basis === "proxied" ? <span className="block text-xs font-normal text-text-muted">short history</span> : null}
      </th>
      <td className="num py-1.5 pr-3 text-right">{formatPercent(p.weight, { decimals: 1 })}</td>
      {p.fit ? (
        <>
          {factors.map((f) => {
            const b = betaOf(p.fit, f.id);
            return (
              <td key={f.id} className={cn("num py-1.5 pr-3 text-right", !isSignificant(b?.t_stat) && "text-text-muted/60")}>
                {b ? b.beta.toFixed(2) : "—"}
              </td>
            );
          })}
          <td className="num py-1.5 text-right">{p.fit.r_squared.toFixed(2)}</td>
        </>
      ) : (
        <td colSpan={factors.length + 1} className="py-1.5 text-text-muted">
          {p.reason ?? "No fit."}
        </td>
      )}
    </tr>
  );
}
