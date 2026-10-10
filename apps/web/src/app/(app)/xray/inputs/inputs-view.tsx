"use client";

import { useCallback, useEffect, useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { parseConstituents, parseRegionLines, type OwnerInputItem } from "@/lib/xray/owner-input";

import { BUTTON, FIELD } from "../supply-chain/edge-form";

const TODAY = () => new Date().toISOString().slice(0, 10);

type Reload = () => Promise<void>;

function SourceFields({
  source,
  setSource,
  asOf,
  setAsOf,
}: {
  source: string;
  setSource: (v: string) => void;
  asOf: string;
  setAsOf: (v: string) => void;
}) {
  return (
    <>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Where you read it</span>
        <input required type="url" className={FIELD} value={source} onChange={(e) => setSource(e.target.value)} placeholder="https://" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">As of</span>
        <input required type="date" className={FIELD} value={asOf} max={TODAY()} onChange={(e) => setAsOf(e.target.value)} />
      </label>
    </>
  );
}

/** Fill inputs from fund holdings the app already stores, so nothing has to be typed. The result
 * keeps the fund's data address and the snapshot date, and is recomputed whenever the fund's
 * holdings are refreshed. */
function FromFunds({ reload }: { reload: Reload }) {
  const [funds, setFunds] = useState<{ isin: string; label: string }[]>([]);
  const [isin, setIsin] = useState("");
  const [region, setRegion] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      // the funds you hold whose holdings are stored, from the X-Ray itself
      const x = await getBrowserApiClient().GET("/portfolio/xray");
      if (x.response.ok && x.data) {
        const list = x.data.funds.filter((f) => f.status === "ok" || f.status === "stale").map((f) => ({ isin: f.isin, label: f.label }));
        setFunds(list);
        setIsin((current) => current || list[0]?.isin || "");
      }
    })();
  }, []);

  async function run(job: () => Promise<{ ok: boolean; changed?: boolean; text?: string }>) {
    setBusy(true);
    setProblem(null);
    setMessage(null);
    const out = await job();
    setBusy(false);
    if (!out.ok) return setProblem(out.text ?? "That did not work.");
    setMessage(out.text ?? (out.changed ? "Saved." : "Already up to date."));
    await reload();
  }

  const client = getBrowserApiClient();
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="No typing" title="Fill these in from your funds" />
      <p className="mt-1 max-w-3xl text-sm text-text-muted">
        A fund that tracks an index publishes its holdings, and the app already stores them. Use one to stand in for the index&apos;s weights, or to spread a region over countries. Each result keeps the fund&apos;s data address and the date of its holdings, is labelled as derived, and is recomputed whenever the fund&apos;s holdings are refreshed.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block font-medium">Fund</span>
          <select className={FIELD} value={isin} onChange={(e) => setIsin(e.target.value)}>
            {funds.map((f) => (
              <option key={f.isin} value={f.isin}>{f.label}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || !isin}
          className={BUTTON}
          onClick={() =>
            void run(async () => {
              const r = await client.POST("/portfolio/xray/inputs/derive/index-weights", { body: { fund_isin: isin } });
              return r.response.ok ? { ok: true, changed: r.data?.changed } : { ok: false, text: "That fund is not one the app looks through, or it has no stored holdings yet." };
            })
          }
        >
          Use as index weights
        </button>
        <button
          type="button"
          disabled={busy}
          className={BUTTON}
          onClick={() =>
            void run(async () => {
              const r = await client.POST("/portfolio/xray/inputs/derive/benchmarks");
              return r.response.ok ? { ok: true, text: r.data?.saved ? `Saved ${r.data.saved} index.` : (r.data?.message ?? undefined) } : { ok: false };
            })
          }
        >
          Do it for my benchmarks
        </button>
      </div>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const r = await client.POST("/portfolio/xray/inputs/derive/regions", { body: { region: region.trim(), fund_isin: isin } });
            return r.response.ok ? { ok: true, changed: r.data?.changed } : { ok: false, text: "That fund cannot spread a region: it is not looked through or has no stored holdings." };
          });
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Spread this region like the fund</span>
          <input required className={FIELD} value={region} onChange={(e) => setRegion(e.target.value)} placeholder="Europe" maxLength={60} />
        </label>
        <button type="submit" disabled={busy || !isin} className={BUTTON}>Spread it</button>
      </form>
      {message ? <p className="mt-2 text-sm text-text-muted" role="status">{message}</p> : null}
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
    </Blueprint>
  );
}

function RegionForm({ reload }: { reload: Reload }) {
  const [region, setRegion] = useState("");
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [asOf, setAsOf] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    const parsed = parseRegionLines(text);
    if (!parsed.ok) return setProblem(parsed.error);
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/xray/inputs/regions", {
      body: { region: region.trim(), countries: parsed.value, source_url: source.trim(), as_of: asOf },
    });
    setBusy(false);
    if (!res.response.ok) return setProblem("That split was not accepted. Check that every country is one the app knows and the source is a web address.");
    setRegion("");
    setText("");
    await reload();
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Revenue by country" title="Spread a region over countries" />
      <p className="mt-1 max-w-3xl text-sm text-text-muted">
        A company that reports revenue for “Europe” gives no country, so the revenue-country view leaves it out. Tell the app how you want a region spread and that revenue is counted in those countries, marked as your estimate.
      </p>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Region name, as the company reports it</span>
          <input required className={FIELD} value={region} onChange={(e) => setRegion(e.target.value)} placeholder="Europe" maxLength={60} />
        </label>
        <div className="hidden sm:block" />
        <label className="text-sm sm:col-span-2">
          <span className="mb-1 block font-medium">Countries and percent, one per line (adding up to 100)</span>
          <textarea
            required
            rows={5}
            className="w-full border border-border bg-surface px-2 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Germany 30\nFrance 20\nUnited Kingdom 25\nItaly 25"}
          />
        </label>
        <SourceFields source={source} setSource={setSource} asOf={asOf} setAsOf={setAsOf} />
        <div className="flex items-end">
          <button type="submit" disabled={busy} className={BUTTON}>Save this split</button>
        </div>
      </form>
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
    </Blueprint>
  );
}

function IndexForm({ reload }: { reload: Reload }) {
  const [label, setLabel] = useState("");
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [asOf, setAsOf] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    const parsed = parseConstituents(text);
    if (!parsed.ok) return setProblem(parsed.error);
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/portfolio/xray/inputs/index-weights", {
      body: { label: label.trim(), constituents: parsed.value, source_url: source.trim(), as_of: asOf },
    });
    setBusy(false);
    if (!res.response.ok) return setProblem("Those weights were not accepted. Check the source is a web address and the weights do not add up to more than 100.");
    setLabel("");
    setText("");
    await reload();
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Against an index" title="Paste an index's weights" />
      <p className="mt-1 max-w-3xl text-sm text-text-muted">
        No provider here supplies an index&apos;s constituents, so paste them from the index provider&apos;s factsheet. The Against a benchmark tab then shows your active share against them. Entering the same name again replaces the earlier list.
      </p>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="text-sm sm:col-span-2">
          <span className="mb-1 block font-medium">Index name</span>
          <input required className={FIELD} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="MSCI World" maxLength={120} />
        </label>
        <label className="text-sm sm:col-span-2">
          <span className="mb-1 block font-medium">One per line: name ; ISIN ; ticker ; weight %</span>
          <textarea
            required
            rows={7}
            className="w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Apple Inc. ; US0378331005 ; AAPL ; 4.9\nMicrosoft Corp ; US5949181045 ; MSFT ; 4.3"}
          />
        </label>
        <SourceFields source={source} setSource={setSource} asOf={asOf} setAsOf={setAsOf} />
        <div className="flex items-end">
          <button type="submit" disabled={busy} className={BUTTON}>Save these weights</button>
        </div>
      </form>
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
    </Blueprint>
  );
}

const KIND_TITLE: Record<OwnerInputItem["kind"], string> = {
  region_split: "Regional splits",
  index_weights: "Index weights",
  supply_edge: "Supply-chain links",
};

function summary(i: OwnerInputItem): string {
  if (i.kind === "region_split") {
    const countries = (i.payload.countries ?? {}) as Record<string, number>;
    return Object.entries(countries).map(([c, s]) => `${c} ${formatPercent(s, { decimals: 0 })}`).join(", ");
  }
  if (i.kind === "index_weights") return `${((i.payload.constituents ?? []) as unknown[]).length} constituents`;
  return String(i.payload.product ?? "");
}

function Entered({ items, reload }: { items: OwnerInputItem[]; reload: Reload }) {
  const [problem, setProblem] = useState<string | null>(null);
  async function remove(id: number) {
    const res = await getBrowserApiClient().DELETE("/portfolio/xray/inputs/{input_id}", { params: { path: { input_id: id } } });
    if (!res.response.ok) return setProblem("That entry could not be removed.");
    setProblem(null);
    await reload();
  }
  const kinds = Object.keys(KIND_TITLE) as OwnerInputItem["kind"][];
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="What you entered" title="Your data, with where it came from" />
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">Nothing entered yet. Everything here is yours: the app shows it as entered by you, never as official data.</p>
      ) : (
        <div className="mt-3 flex flex-col gap-4">
          {kinds.map((kind) => {
            const rows = items.filter((i) => i.kind === kind);
            if (rows.length === 0) return null;
            return (
              <section key={kind}>
                <h3 className="text-sm font-semibold">{KIND_TITLE[kind]}</h3>
                <ul className="mt-1 divide-y divide-border-soft border border-border">
                  {rows.map((i) => (
                    <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-sm">
                      <span>
                        <span className="font-medium">{i.key}</span>
                        <span className="ml-2 text-xs text-text-muted">{i.origin === "derived" ? "derived from a fund" : "entered by you"}</span>
                        <span className="ml-2 text-text-muted">{summary(i)}</span>
                        <span className="ml-2 text-xs text-text-muted">as of {formatDate(i.as_of)}</span>
                      </span>
                      <span className="flex items-center gap-3">
                        <a href={i.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Source</a>
                        <button type="button" onClick={() => void remove(i.id)} className="text-text-muted hover:text-text">Remove</button>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
      {problem ? <p className="mt-2 text-sm text-loss" role="alert">{problem}</p> : null}
    </Blueprint>
  );
}

export function InputsView() {
  const [items, setItems] = useState<OwnerInputItem[] | null>(null);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    const res = await getBrowserApiClient().GET("/portfolio/xray/inputs");
    if (res.response.ok && res.data) {
      setItems(res.data);
      setFailed(false);
    } else setFailed(true);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the list once on mount
    void reload();
  }, [reload]);

  return (
    <div className="flex flex-col gap-5">
      {failed ? <p className="text-sm text-loss" role="alert">Your entries could not be loaded.</p> : null}
      <Entered items={items ?? []} reload={reload} />
      <FromFunds reload={reload} />
      <RegionForm reload={reload} />
      <IndexForm reload={reload} />
    </div>
  );
}
