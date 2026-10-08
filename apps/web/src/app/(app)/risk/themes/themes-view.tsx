"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText, SaveBar, SavedList, SELECT } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import type { ThemeExposure, ThemeSelector } from "@/lib/risk/risk";

import { Notes, Pct, TD, TH, TH_LEFT, Warnings } from "../../xray/shared";

const fetchOptions = () => getBrowserApiClient().GET("/portfolio/scenarios/themes/options");

type Options = NonNullable<Awaited<ReturnType<typeof fetchOptions>>["data"]>["options"];

const KINDS: { value: ThemeSelector["kind"]; label: string }[] = [
  { value: "sector", label: "Sector" },
  { value: "industry", label: "Industry" },
  { value: "country", label: "Country of the company" },
  { value: "revenue_country", label: "Country it earns money in" },
  { value: "domicile", label: "Country it is based in" },
  { value: "listing", label: "Where it is listed" },
  { value: "currency", label: "Currency" },
  { value: "commodity", label: "Commodity it depends on" },
  { value: "trade_product", label: "Traded product" },
  { value: "issuer", label: "A named company" },
];

const CONFIDENCE: Record<ThemeExposure["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export function ThemesView() {
  const { state } = useApiResource(fetchOptions);
  return (
    <ResourceGate state={state} label="theme choices">
      {(data) => <Builder options={data.options} />}
    </ResourceGate>
  );
}

function Builder({ options }: { options: Options }) {
  const [name, setName] = useState("");
  const [selectors, setSelectors] = useState<ThemeSelector[]>([]);
  const [kind, setKind] = useState<ThemeSelector["kind"]>("sector");
  const [value, setValue] = useState("");
  const [result, setResult] = useState<ThemeExposure | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const choices = options[kind] ?? [];

  function add() {
    const v = value.trim();
    if (v === "") return setProblem("Choose or type a value to add.");
    if (selectors.some((s) => s.kind === kind && s.value.toLowerCase() === v.toLowerCase())) return setProblem("That one is already in the theme.");
    setProblem(null);
    setSelectors([...selectors, { kind, value: v }]);
    setValue("");
  }

  const definition = () => ({ name: name.trim() || "Untitled theme", selectors });

  async function run() {
    if (selectors.length === 0) return setProblem("Add at least one thing the theme is made of.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/themes", { body: definition() });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The theme could not be measured."));
    setResult(res.data);
  }

  async function save(saveName: string): Promise<string | null> {
    const res = await getBrowserApiClient().POST("/portfolio/scenarios/themes/save", { body: { name: saveName, theme: definition() } });
    return res.response.ok ? null : problemText(res.error, "The theme could not be saved.");
  }

  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Define" title="What counts as part of the theme" />
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Combine anything: a sector, a country it earns in, a commodity it depends on. A holding counts once, at its largest match, so overlapping
          choices do not add up twice.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
          <Field label="Theme name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Semiconductors" maxLength={80} />
          </Field>
          <Field label="Type">
            <select
              className={SELECT}
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as ThemeSelector["kind"]);
                setValue("");
              }}
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Value" hint={choices.length > 0 ? `${choices.length} in your portfolio, largest first` : "Type it as it appears in the data"}>
            <Input list="theme-values" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Choose or type" />
            <datalist id="theme-values">
              {choices.map((c) => (
                <option key={c.value} value={c.value}>
                  {`${c.label} (${formatPercent(c.weight, { decimals: 1 })})`}
                </option>
              ))}
            </datalist>
          </Field>
          <Button type="button" variant="outline" onClick={add}>
            Add
          </Button>
        </div>
        {selectors.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-2">
            {selectors.map((s) => (
              <li key={`${s.kind}:${s.value}`} className="flex items-center gap-2 border border-border px-2 py-1 text-sm">
                <span className="text-text-muted">{KINDS.find((k) => k.value === s.kind)?.label}</span>
                <span className="font-medium">{s.value}</span>
                <button
                  type="button"
                  aria-label={`Remove ${s.value}`}
                  className="text-text-muted hover:text-text focus-visible:outline-2 focus-visible:outline-focus"
                  onClick={() => setSelectors(selectors.filter((x) => x !== s))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="mt-3 flex items-center gap-3">
          <Button type="button" onClick={run} disabled={busy}>
            {busy ? "Measuring…" : "Measure exposure"}
          </Button>
        </div>
        <div className="mt-3">
          <Problem>{problem}</Problem>
        </div>
      </Blueprint>

      {result ? <Result result={result} saveBar={<SaveBar save={save} onSaved={() => setRefresh((n) => n + 1)} />} /> : null}

      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Kept" title="Saved themes" />
        <div className="mt-3">
          <SavedList
            kind="theme_exposure"
            refreshKey={refresh}
            onOpen={(payload) => {
              const saved = payload.result as ThemeExposure;
              setResult(saved);
              setName(saved.theme.name);
              setSelectors(saved.theme.selectors);
            }}
          />
        </div>
      </Blueprint>
    </div>
  );
}

function Result({ result, saveBar }: { result: ThemeExposure; saveBar: React.ReactNode }) {
  const unknownReasons = Object.entries(result.unknown.reasons).sort((a, b) => b[1] - a[1]);
  const unknownShare = result.exposed_weight + result.unknown.weight > 0 ? result.unknown.weight / (result.exposed_weight + result.unknown.weight) : 0;
  return (
    <Blueprint className="p-4">
      <PanelHeading level={2} kicker={CONFIDENCE[result.confidence]} title={result.theme.name} />
      <Warnings warnings={result.warnings} />
      <p className="mt-1 text-sm text-text-muted">{result.confidence_reason}</p>

      <div className="mt-4" role="img" aria-label={`Exposed ${formatPercent(result.exposed_weight)}, unknown ${formatPercent(result.unknown.weight)}`}>
        <div className="flex h-5 w-full border border-border">
          <span className="bg-accent" style={{ width: `${Math.min(100, result.exposed_weight * 100)}%` }} />
          <span
            className="bg-[repeating-linear-gradient(135deg,var(--border)_0_3px,transparent_3px_6px)]"
            style={{ width: `${Math.min(100 - result.exposed_weight * 100, result.unknown.weight * 100)}%` }}
          />
        </div>
        <p className="mt-1 text-xs text-text-muted">Solid: found in the theme. Hatched: could not be told.</p>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="In the theme" value={result.exposed_weight} note={`${formatPercent(result.direct_weight)} held directly, ${formatPercent(result.fund_weight)} through funds`} />
        <Stat label="Could be as much as" value={result.upper_bound} note="if every unknown turned out to belong" />
        <Stat label="Not known" value={result.unknown.weight} note={`${formatPercent(unknownShare, { decimals: 0 })} of what was checked`} />
        <Stat label="Inferred, not reported" value={result.inferred_share} note="share of the figure that rests on an estimate" />
      </dl>

      {unknownReasons.length > 0 ? (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-medium">Why part is unknown</h3>
          <ul className="divide-y divide-border-soft border border-border-soft text-sm">
            {unknownReasons.map(([reason, w]) => (
              <li key={reason} className="flex justify-between gap-3 px-3 py-1.5">
                <span>{reason}</span>
                <Pct value={w} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-text-muted">
            <tr className="border-b border-border">
              <th className={TH_LEFT}>Company</th>
              <th className={TH}>In the theme</th>
              <th className={TH}>Directly</th>
              <th className={TH}>Via funds</th>
              <th className={TH_LEFT}>Through</th>
            </tr>
          </thead>
          <tbody>
            {result.issuers.map((i) => (
              <tr key={i.issuer} className="border-b border-border-soft align-top">
                <td className="px-3 py-1.5 font-medium">{i.issuer}</td>
                <td className={TD}>
                  <Pct value={i.exposed} decimals={2} />
                </td>
                <td className={TD}>
                  <Pct value={i.direct} decimals={2} />
                </td>
                <td className={TD}>
                  <Pct value={i.via_funds} decimals={2} />
                </td>
                <td className="px-3 py-1.5 text-xs text-text-muted">
                  {i.paths.map((p) => (
                    <div key={`${p.channel}-${p.basis}`}>
                      {p.channel}: {p.basis}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
            {result.issuers.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-3 text-text-muted">
                  Nothing in your portfolio matched this theme.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="mt-4 space-y-3">
        <Notes notes={[result.method]} title="How this is counted" />
        <div className="border-t border-border-soft pt-3">{saveBar}</div>
      </div>
    </Blueprint>
  );
}

function Stat({ label, value, note }: { label: string; value: number; note: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="mt-0.5 text-2xl leading-tight font-semibold">
        <Pct value={value} />
      </dd>
      <p className="mt-0.5 text-xs text-text-muted">{note}</p>
    </div>
  );
}
