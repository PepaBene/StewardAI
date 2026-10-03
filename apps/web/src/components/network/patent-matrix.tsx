"use client";

import { useMemo, useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatDate } from "@/lib/format";
import { formatNumber } from "@/lib/charts/format";
import { bubbleSize, buildPatentMatrix, cellKey, citationLevel, familyDetailMap, humanize, type MatrixCell, type PatentColourMode } from "@/lib/network/logic";
import type { PatentFamily, PatentTrendRow } from "@/lib/network/types";
import { cn } from "@/lib/utils";

import { SourceLink } from "./badges";

const CITATION_BG = ["bg-surface-muted border border-dashed border-text-muted", "bg-heat-up-soft", "bg-heat-up", "bg-heat-up-strong"] as const;
const CITATION_LABEL = ["no citations", "few citations", "some citations", "many citations"] as const;

const STATUS_BG: Record<string, string> = {
  granted: "bg-categorical-1",
  active: "bg-categorical-2",
  application: "bg-categorical-4",
  expired: "bg-categorical-8",
  mixed: "bg-categorical-6",
  unknown: "bg-surface-muted border border-dashed border-text-muted",
};

function statusClass(s: string): string {
  return STATUS_BG[s] ?? STATUS_BG.unknown!;
}

function cellColour(cell: MatrixCell, mode: PatentColourMode, maxCitations: number): { cls: string; label: string } {
  if (mode === "status") return { cls: statusClass(cell.dominantStatus), label: cell.dominantStatus === "mixed" ? "mixed legal status" : `legal status ${cell.dominantStatus}` };
  if (cell.citationsUnknown === cell.families) return { cls: CITATION_BG[0], label: "citation counts unknown" };
  const level = citationLevel(cell.citations, maxCitations);
  return { cls: CITATION_BG[level], label: CITATION_LABEL[level]! };
}

/**
 * Patent landscape: technology class (CPC subclass) x priority year. Bubble area = distinct
 * patent families; colour = forward citations or legal status. Counts and citations are
 * signals, not patent value or a freedom-to-operate view. Only families with a dated ownership
 * path to this holding are drawn, so a subsidiary's patents are never silently added.
 */
export function PatentMatrix({
  patents,
  trends,
  trendsStatus = "ok",
  asOfDate,
  knownAt,
  limitations,
}: {
  patents: readonly PatentFamily[];
  trends: readonly PatentTrendRow[] | null;
  trendsStatus?: "ok" | "loading" | "unavailable";
  asOfDate: string | null;
  knownAt: string | null;
  limitations: readonly string[];
}) {
  const [mode, setMode] = useState<PatentColourMode>("citations");
  const [pick, setPick] = useState<{ subclass: string; year: number } | null>(null);
  const m = useMemo(() => buildPatentMatrix(patents), [patents]);
  const byKey = useMemo(() => familyDetailMap(patents), [patents]);
  const picked = pick ? m.cells.get(cellKey(pick.subclass, pick.year)) : undefined;
  const sources = [...new Set(patents.map((p) => p.source))].join(", ") || "EPO OPS and owner entries";

  const takeaway =
    m.placedFamilies === 0
      ? "No patent family with a dated ownership path to this company is stored."
      : `${m.placedFamilies} distinct families with a dated ownership path span ${m.subclasses.length} technology classes in the grid; a family in several classes appears in each, so rows are not additive.`;

  const statusKinds = [...new Set(m.cells.size ? [...m.cells.values()].flatMap((c) => Object.keys(c.statusCounts)) : [])];

  return (
    <ChartFrame
      id="network-patents"
      kicker="Innovation"
      title="Patent landscape"
      takeaway={takeaway}
      units={`Rows: CPC subclass. Columns: priority year. Bubble area: distinct families (largest cell ${m.maxFamilies}). Colour: ${mode === "citations" ? "forward citations summed over the cell's families" : "legal status"}. ${m.attributedFamilies} attributed families; ${m.placedFamilies} placed${m.missingPriorityDate ? `, ${m.missingPriorityDate} without a priority date` : ""}${m.missingCpc ? `, ${m.missingCpc} without a CPC class` : ""}${m.unattributedFamilies ? `; ${m.unattributedFamilies} listed families have no dated ownership path and are excluded` : ""}. Families from a source that supplies no citation counts (EPO OPS) show citations as unknown, never as zero.`}
      provenance={{
        source: `Patent families (${sources}), reconciled to the issuer`,
        asOf: asOfDate,
        knownAt,
        method: "Families deduplicated across offices; attribution needs a dated ownership path (direct applicant or confirmed subsidiary).",
        state: m.placedFamilies === 0 ? "not_covered" : m.unattributedFamilies + m.missingPriorityDate + m.missingCpc > 0 ? "partial" : "verified",
        reason: m.placedFamilies === 0 ? "No attributed patent families are stored. An EPO OPS refresh is an owner action below." : null,
        coverage: m.attributedFamilies > 0 ? { covered: m.placedFamilies, total: m.attributedFamilies, unit: "attributed families placed" } : null,
      }}
      hasValue={m.placedFamilies > 0}
      controls={
        <Segmented
          label="Colour by"
          value={mode}
          onChange={setMode}
          options={[
            { id: "citations", label: "Citations" },
            { id: "status", label: "Legal status" },
          ]}
        />
      }
      table={{
        caption: "Patent families by CPC subclass and priority year",
        columns: ["Subclass", "Year", "Families", "Forward citations", "Citation counts unknown", "Legal status"],
        rows: [...m.cells.values()]
          .sort((a, b) => a.subclass.localeCompare(b.subclass) || a.year - b.year)
          .map((c) => [
            c.subclass,
            c.year,
            c.families,
            c.citationsUnknown === c.families ? "unknown" : formatNumber(c.citations),
            c.citationsUnknown,
            Object.entries(c.statusCounts)
              .map(([k, v]) => `${k} ${v}`)
              .join(", "),
          ]),
      }}
    >
      <div className="flex flex-col gap-3">
        <div className="relative overflow-x-auto">
          <table className="border-separate border-spacing-0.5 text-xs">
            <caption className="sr-only">Technology class by priority year; select a cell to list its families</caption>
            <thead>
              <tr>
                <td className="w-16" />
                {m.years.map((y) => (
                  <th key={y} scope="col" className="num px-0.5 pb-1 text-center text-[11px] font-normal text-text-muted">
                    {m.years.length > 12 ? String(y).slice(2) : y}
                    <span className="sr-only">{y}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.subclasses.map((s) => (
                <tr key={s}>
                  <th scope="row" className="pr-2 text-left font-mono text-xs font-semibold">
                    {s}
                  </th>
                  {m.years.map((y) => {
                    const cell = m.cells.get(cellKey(s, y));
                    const size = cell ? bubbleSize(cell.families, m.maxFamilies) : 0;
                    const colour = cell ? cellColour(cell, mode, m.maxCitations) : null;
                    const isPick = pick?.subclass === s && pick.year === y;
                    return (
                      <td key={y} className={cn("h-9 w-9 p-0 text-center", isPick && "outline-2 outline-focus")}>
                        {cell && colour ? (
                          <button
                            type="button"
                            aria-label={`${s}, ${y}: ${cell.families} families, ${colour.label}`}
                            aria-pressed={isPick}
                            title={`${s} ${y}: ${cell.families} families; ${formatNumber(cell.citations)} citations${cell.citationsUnknown ? ` (${cell.citationsUnknown} families unknown)` : ""}`}
                            onClick={() => setPick(isPick ? null : { subclass: s, year: y })}
                            className="flex h-9 w-9 items-center justify-center outline-hidden focus-visible:outline-2 focus-visible:outline-focus"
                          >
                            <span className={cn("block rounded-full", colour.cls)} style={{ width: size, height: size }} />
                          </button>
                        ) : (
                          <span className="block h-9 w-9" aria-hidden="true" />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {m.olderYearsDropped > 0 ? <p className="text-xs text-text-muted">The year axis is capped at 60 years from the newest; families with a priority year before {m.years[0]} are counted but not drawn.</p> : null}
        {m.hiddenSubclasses > 0 ? <p className="text-xs text-text-muted">{m.hiddenSubclasses} smaller technology classes are not drawn; the table view lists only the drawn ones.</p> : null}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted" aria-label="Legend">
          {mode === "citations"
            ? CITATION_BG.map((cls, i) => (
                <span key={i} className="inline-flex items-center gap-1.5">
                  <span className={cn("inline-block size-3 rounded-full", cls)} aria-hidden="true" />
                  {i === 0 ? "No citations, or counts not provided by the source" : i === 1 ? "Few" : i === 2 ? "Some" : "Many (relative to the most-cited cell)"}
                </span>
              ))
            : [...new Set([...statusKinds, "mixed"])].map((k) => (
                <span key={k} className="inline-flex items-center gap-1.5">
                  <span className={cn("inline-block size-3 rounded-full", statusClass(k))} aria-hidden="true" />
                  {humanize(k)}
                </span>
              ))}
        </div>
        {picked ? (
          <section aria-label={`Families in ${picked.subclass} ${picked.year}`} className="flex flex-col gap-1.5 border border-border p-3 text-sm">
            <h3 className="text-base font-semibold">
              {picked.subclass}, priority {picked.year}: {picked.families} {picked.families === 1 ? "family" : "families"}
            </h3>
            <ul className="flex flex-col gap-2">
              {picked.familyKeys.map((k) => {
                const p = byKey.get(k);
                if (!p) return null;
                return (
                  <li key={k} className="border-t border-border-soft pt-1.5 first:border-0 first:pt-0">
                    <p className="font-medium">
                      <SourceLink href={p.sourceUrl}>{p.title ?? p.familyKey}</SourceLink>
                    </p>
                    <p className="num text-xs text-text-muted">
                      {p.familyKey} · priority {p.priorityDate ? formatDate(p.priorityDate) : "unknown"} · {p.legalStatus} · forward citations {p.citationsForward ?? "unknown"} ·{" "}
                      {p.jurisdictions.length ? p.jurisdictions.join(", ") : "jurisdictions not stated"}
                    </p>
                    <p className="text-xs text-text-muted">
                      Applicant {p.applicant ?? "not stated"}; ownership basis {p.ownershipBasis ?? "not stated"}
                      {p.ownershipConfidence !== null ? `, confidence ${Math.round(p.ownershipConfidence * 100)}%` : ""}
                      {p.ownershipValidFrom ? `, valid from ${p.ownershipValidFrom}` : ""}.
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
        <p className="border-l-2 border-border pl-3 text-xs text-text">
          Patent counts and citations are research signals, not a measure of patent value, and not a freedom-to-operate or infringement opinion.
        </p>
        {trends && trends.length > 0 ? <TrendTable rows={trends} /> : null}
        {trendsStatus === "unavailable" ? <p role="alert" className="text-xs text-text">Grant and expiry timing could not be loaded; its absence here does not mean there is none.</p> : null}
        {limitations.length ? <p className="text-xs text-text-muted">{limitations.join(" ")}</p> : null}
      </div>
    </ChartFrame>
  );
}

function TrendTable({ rows }: { rows: readonly PatentTrendRow[] }) {
  const sorted = [...rows].sort((a, b) => b.families - a.families || a.cpc.localeCompare(b.cpc) || a.year - b.year).slice(0, 15);
  return (
    <details className="border border-border-soft text-xs">
      <summary className="cursor-pointer px-3 py-1.5 font-medium text-accent-ink select-none hover:underline">Grant and expiry timing (current attribution, top {sorted.length} class-years)</summary>
      <div className="overflow-x-auto border-t border-border-soft">
        <table className="w-full min-w-[34rem]">
          <caption className="sr-only">Grant timing, expiry and legal status by CPC code and priority year</caption>
          <thead className="bg-surface-muted text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <tr>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">CPC</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Year</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Families</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Median years to grant</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Expiring within 5y</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Expired</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={`${r.cpc}|${r.year}`} className="border-t border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-mono font-normal">{r.cpc}</th>
                <td className="num px-3 py-1.5 text-right">{r.year}</td>
                <td className="num px-3 py-1.5 text-right">{r.families}</td>
                <td className="num px-3 py-1.5 text-right">{r.medianYearsToGrant ?? "unknown"}</td>
                <td className="num px-3 py-1.5 text-right">{r.expiringWithin5y}{r.expiryIsEstimated ? " (est.)" : ""}</td>
                <td className="num px-3 py-1.5 text-right">{r.expired}{r.expiryIsEstimated ? " (est.)" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-3 py-1.5 text-text-muted">
          The trends route reports the current attribution only (it takes no as-of date). Rows are CPC code x year and a family counts once per CPC code, so they are not additive. (est.) means expiry is estimated as 20 years from priority.
        </p>
      </div>
    </details>
  );
}
