"use client";

import { useState, type ReactNode } from "react";

import { DrawdownChart, Dumbbell, GridStatus, LorenzChart, Treemap, WaterfallChart } from "@/components/charts";
import { BandGauge, VALUATION_RANGE, VALUATION_ZONES } from "@/components/insight/band-gauge";
import { DotStrip } from "@/components/insight/dot-strip";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { LimitedState } from "@/components/insight/limited-state";
import { ScoreMark } from "@/components/insight/score-mark";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CompactRow } from "@/components/ui/compact-row";
import { FilterChips } from "@/components/ui/filter-chips";
import { PromptCard } from "@/components/ui/prompt-card";
import { ReasonOnce } from "@/components/ui/reason-once";
import { ResponsiveTable } from "@/components/ui/responsive-table";
import { SkeletonPlot, SkeletonRows, SkeletonStrip } from "@/components/ui/skeleton";
import type { MarkPetal } from "@/lib/insight/insight";

/*
 * Every shared primitive with sample data, in both themes (toggle in the account menu), so a
 * change to tokens or a component can be checked in one place. Not linked from the nav.
 */

const PETALS: MarkPetal[] = [
  { axis: "value", fraction: 0.35, notApplicable: false },
  { axis: "future", fraction: 0.8, notApplicable: false },
  { axis: "past", fraction: 0.65, notApplicable: false },
  { axis: "health", fraction: null, notApplicable: false },
  { axis: "dividend", fraction: 0.2, notApplicable: false },
];

const HOLDINGS = [
  { t: "AAPL", name: "Apple", w: 0.18, pl: 0.22, fv: 0.41 },
  { t: "MSFT", name: "Microsoft", w: 0.14, pl: 0.31, fv: 0.12 },
  { t: "NVDA", name: "Nvidia", w: 0.12, pl: 0.84, fv: 0.6 },
  { t: "ASML", name: "ASML", w: 0.08, pl: -0.06, fv: -0.18 },
  { t: "NOVO", name: "Novo Nordisk", w: 0.07, pl: -0.21, fv: -0.32 },
  { t: "KO", name: "Coca-Cola", w: 0.05, pl: 0.04, fv: 0.03 },
  { t: "VWRL", name: "Vanguard All-World", w: 0.36, pl: 0.12, fv: null },
];

const LEVELS = Array.from({ length: 120 }, (_, i) => ({
  date: new Date(Date.UTC(2026, 0, 1 + i * 2)).toISOString().slice(0, 10),
  value: 100 + 10 * Math.sin(i / 9) + i * 0.15 - (i > 60 && i < 80 ? 9 : 0),
}));

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="panel-title">{title}</h2>
      {children}
    </section>
  );
}

export function Gallery() {
  const [chip, setChip] = useState("all");
  return (
    <div className="flex flex-col gap-10">
      <PageHeader title="Component gallery" description="Shared components with sample data. Switch the theme from the account menu to check both." />

      <Section title="Answer strip and badges">
        <KpiStrip
          tiles={[
            { label: "Value", value: "CZK 1 284 000", change: { text: "+0.84%", direction: "up", period: "today" } },
            { label: "Total return vs benchmark", value: "+4.1 pp", signed: "gain", verdict: { tone: "good", text: "Ahead of the benchmark this year" } },
            { label: "Unrealised P/L", value: "CZK 182 400", signed: "gain" },
            { label: "Income next 12 months", value: "CZK 21 300", note: "From 9 payers" },
          ]}
        />
        <div className="flex flex-wrap gap-2">
          {(["limited", "estimated", "stale", "na", "new", "kind", "gain", "loss", "fair-value"] as const).map((v) => (
            <Badge key={v} variant={v}>
              {v}
            </Badge>
          ))}
        </div>
        <FilterChips
          label="Sample chips"
          value={chip}
          onChange={setChip}
          chips={[
            { key: "all", label: "All", count: 24 },
            { key: "earnings", label: "Earnings", count: 10, unread: true },
            { key: "analyst", label: "Analyst", count: 6 },
          ]}
        />
      </Section>

      <Section title="Score marks">
        <div className="flex flex-wrap items-end gap-6">
          <ScoreMark petals={PETALS} size="row" label="Sample company" />
          <ScoreMark petals={PETALS} size="header" label="Sample company" />
          <ScoreMark petals={PETALS} size="sidebar" label="Sample company" />
          <ScoreMark petals={PETALS} size="card" label="Sample company" />
        </div>
      </Section>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Section title="Band gauge">
          <div className="blueprint px-5 py-5">
            <BandGauge
              value={0.407}
              min={VALUATION_RANGE.min}
              max={VALUATION_RANGE.max}
              zones={VALUATION_ZONES}
              marker="Price USD 342, 40.7% above fair value"
              format={(v) => `${Math.round(v * 100)}%`}
              label="Price against fair value"
            />
          </div>
        </Section>
        <Section title="Dot strip">
          <div className="blueprint px-5 py-5">
            <DotStrip
              convention="price-vs-fair"
              sizeByWeight
              label="Price against fair value"
              format={(v) => `${Math.round(v * 100)}%`}
              dots={HOLDINGS.map((h) => ({ key: h.t, label: h.t, value: h.fv, weight: h.w }))}
            />
          </div>
        </Section>
        <Section title="Concentration curve">
          <div className="blueprint px-5 py-5">
            <LorenzChart weights={HOLDINGS.map((h) => h.w)} names={HOLDINGS.map((h) => h.name)} />
          </div>
        </Section>
        <Section title="Before and after">
          <div className="blueprint px-5 py-5">
            <Dumbbell
              beforeLabel="Now"
              afterLabel="After rebalance"
              formatValue={(v) => `${v.toFixed(1)}%`}
              rows={HOLDINGS.map((h, i) => ({ label: h.name, before: h.w * 100, after: h.w * 100 + (i % 2 ? -2 : 3), target: { low: 5, high: 10 } }))}
            />
          </div>
        </Section>
        <Section title="Underwater">
          <div className="blueprint px-5 py-5">
            <DrawdownChart data={LEVELS} label="Portfolio" />
          </div>
        </Section>
        <Section title="Waterfall">
          <div className="blueprint px-5 py-5">
            <WaterfallChart
              label="Sample return bridge"
              steps={[
                { id: "start", label: "Start", value: 1000, kind: "total" },
                { id: "deposits", label: "Deposits", value: 120, kind: "delta" },
                { id: "price", label: "Price", value: 84, kind: "delta" },
                { id: "fx", label: "Currency", value: -31, kind: "delta" },
                { id: "income", label: "Income", value: 18, kind: "delta" },
                { id: "costs", label: "Costs", value: -6, kind: "delta" },
                { id: "other", label: "Not attributed", value: 0, kind: "delta", derived: true },
                { id: "end", label: "End", value: 1185, kind: "total" },
              ]}
            />
          </div>
        </Section>
        <Section title="Treemap">
          <div className="blueprint px-5 py-5">
            <Treemap items={HOLDINGS.map((h) => ({ id: h.t, label: h.t, weight: h.w, change: h.pl }))} />
          </div>
        </Section>
        <Section title="Status matrix">
          <div className="blueprint px-5 py-5">
            <GridStatus
              columns={Array.from({ length: 14 }, (_, i) => `${i + 1} Oct`)}
              rows={[
                { label: "Prices", group: "Market data", cells: Array.from({ length: 14 }, (_, i) => (i === 4 ? "retried" : "ok")), note: "18:10" },
                { label: "News", group: "Market data", cells: Array.from({ length: 14 }, (_, i) => (i === 9 ? "failed" : "ok")) },
                { label: "13F", group: "Filings", cells: Array.from({ length: 14 }, (_, i) => (i % 7 === 0 ? "ok" : "none")) },
                { label: "Estimates", group: "Filings", cells: Array.from({ length: 14 }, () => "unknown") },
              ]}
            />
          </div>
        </Section>
      </div>

      <Section title="Limited state, caveats and prompts">
        <div className="blueprint px-5 py-5">
          <LimitedState fixHref="/config/jobs" since="16 Oct">
            Needs 250 days of prices, has 196.
          </LimitedState>
        </div>
        <ReasonOnce reasons={["Beta is missing for 4 holdings.", "Expected shortfall needs 250 observations, has 196.", "beta is missing for 4 holdings."]} />
        <PromptCard title="Write down why you bought 6 holdings" action={<Button size="sm">Start</Button>} onDismiss={() => undefined}>
          A thesis per holding lets the app tell you when the reason no longer holds.
        </PromptCard>
      </Section>

      <Section title="Responsive table and compact rows">
        <div className="blueprint px-2 py-2">
          <ResponsiveTable
            caption="Sample holdings"
            csvName="sample-holdings.csv"
            pageSize={4}
            rows={HOLDINGS}
            rowKey={(h) => h.t}
            columns={[
              { key: "name", header: "Holding", cell: (h) => h.name, csv: (h) => h.name },
              { key: "w", header: "Weight", numeric: true, cell: (h) => `${(h.w * 100).toFixed(1)}%`, csv: (h) => h.w },
              { key: "pl", header: "P/L", numeric: true, cell: (h) => <span className={h.pl >= 0 ? "text-gain" : "text-loss"}>{`${(h.pl * 100).toFixed(1)}%`}</span>, csv: (h) => h.pl },
            ]}
            compact={(h) => (
              <CompactRow
                avatar={<ScoreMark petals={PETALS} size="row" label={h.name} />}
                name={h.name}
                symbol={h.t}
                primary={`${(h.w * 100).toFixed(1)}%`}
                secondary={[`${(h.pl * 100).toFixed(1)}% P/L`]}
                href="#"
              />
            )}
          />
        </div>
      </Section>

      <Section title="Skeletons">
        <SkeletonStrip />
        <div className="grid gap-6 md:grid-cols-2">
          <div className="blueprint px-5 py-5">
            <SkeletonPlot height={160} />
          </div>
          <div className="blueprint px-5 py-5">
            <SkeletonRows rows={4} />
          </div>
        </div>
      </Section>
    </div>
  );
}
