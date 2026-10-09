import type { Settings } from "./performance";

export type BenchmarkRow = { symbol: string; weight: string; currency: string; basis: "total" | "price" | "unknown" };

export type SplitRow = { ticker: string; day: string; ratio: string; note: string };

export type MergerRow = { old_ticker: string; new_ticker: string; day: string; ratio: string; note: string };

export type SpinOffRow = { parent_ticker: string; child_ticker: string; day: string; ratio: string; cost_percent: string; note: string };

export type SettingsForm = {
  splits: SplitRow[];
  mergers: MergerRow[];
  spin_offs: SpinOffRow[];
  core_tickers: string;
  cost_method: "fifo" | "average";
  time_test_years: string;
  time_test_warning_months: string;
  exemption_limit: string;
  exemption_currency: string;
  sleeve_gap_points: string;
  sleeve_lookback_years: string;
  cash_tolerance: string;
  alert_severity: string;
  benchmark: BenchmarkRow[];
  rebalance: "none" | "monthly" | "quarterly" | "annual";
  note: string;
};

export function settingsToForm(s: Settings): SettingsForm {
  return {
    core_tickers: (s.core_tickers ?? []).join(", "),
    cost_method: s.cost_method,
    time_test_years: String(s.time_test_years),
    time_test_warning_months: String(s.time_test_warning_months),
    exemption_limit: String(s.exemption_limit),
    exemption_currency: s.exemption_currency,
    sleeve_gap_points: String(s.sleeve_gap_points),
    sleeve_lookback_years: String(s.sleeve_lookback_years),
    cash_tolerance: String(s.cash_tolerance),
    alert_severity: String(s.alert_severity ?? 2),
    benchmark: (s.benchmark?.components ?? []).map((c) => ({
      symbol: c.symbol,
      weight: String(Number(c.weight) * 100),
      currency: c.currency,
      basis: c.basis ?? "unknown",
    })),
    rebalance: s.benchmark?.rebalance ?? "none",
    splits: (s.splits ?? []).map((x) => ({ ticker: x.ticker, day: x.day, ratio: String(x.ratio), note: x.note ?? "" })),
    mergers: (s.mergers ?? []).map((x) => ({
      old_ticker: x.old_ticker,
      new_ticker: x.new_ticker,
      day: x.day,
      ratio: String(x.ratio),
      note: x.note ?? "",
    })),
    spin_offs: (s.spin_offs ?? []).map((x) => ({
      parent_ticker: x.parent_ticker,
      child_ticker: x.child_ticker,
      day: x.day,
      ratio: String(x.ratio),
      cost_percent: String(Math.round(Number(x.cost_share) * 1e6) / 1e4),
      note: x.note ?? "",
    })),
    note: s.note ?? "",
  };
}

function wholeNumber(label: string, raw: string, min: number, max: number, errors: string[]): number {
  const n = Number(raw);
  if (raw.trim() === "" || !Number.isInteger(n) || n < min || n > max) errors.push(`${label} must be a whole number from ${min} to ${max}.`);
  return n;
}

function amount(label: string, raw: string, min: number, max: number, errors: string[]): number {
  const n = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(n) || n < min || n > max) errors.push(`${label} must be a number from ${min} to ${max}.`);
  return n;
}

/** The backend accepts weights within 1e-6 of 1, i.e. 1e-4 percentage points of 100. */
const WEIGHT_TOLERANCE_POINTS = 1e-4;

export type FormResult ={ ok: true; body: Settings } | { ok: false; errors: string[] };

/** Validates the form the way the backend will, so mistakes show before saving. */
export function formToSettings(form: SettingsForm): FormResult {
  const errors: string[] = [];
  const tickers = form.core_tickers
    .split(/[\s,;]+/)
    .map((t) => t.trim())
    .filter(Boolean);
  if (new Set(tickers).size !== tickers.length) errors.push("A core ticker is listed twice.");
  const years = wholeNumber("Holding period", form.time_test_years, 1, 10, errors);
  const warn = wholeNumber("Warning window", form.time_test_warning_months, 1, 24, errors);
  const lookback = wholeNumber("Comparison window", form.sleeve_lookback_years, 1, 10, errors);
  const limit = amount("Exemption limit", form.exemption_limit, 0, 1e12, errors);
  const gap = amount("Gap limit", form.sleeve_gap_points, 0, 50, errors);
  const tolerance = amount("Cash tolerance", form.cash_tolerance, 0, 1e9, errors);
  const severity = wholeNumber("Alert severity", form.alert_severity, 0, 3, errors);
  const currency = form.exemption_currency.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(currency)) errors.push("The exemption currency must be a three-letter code such as CZK.");

  const rows = form.benchmark.filter((r) => r.symbol.trim() !== "" || r.weight.trim() !== "");
  let benchmark: Settings["benchmark"] = null;
  if (rows.length > 0) {
    const weights = rows.map((r) => Number(r.weight));
    if (rows.some((r, i) => r.symbol.trim() === "" || !Number.isFinite(weights[i]) || weights[i] <= 0 || weights[i] > 100)) {
      errors.push("Every benchmark line needs a symbol and a weight between 0 and 100.");
    } else if (Math.abs(weights.reduce((a, b) => a + b, 0) - 100) > WEIGHT_TOLERANCE_POINTS) {
      errors.push("Benchmark weights must add up to 100 (use more decimals, such as 33.333333, for thirds).");
    }
    if (rows.some((r) => !/^[A-Za-z]{3}$/.test(r.currency.trim()))) errors.push("Every benchmark line needs a three-letter currency code.");
    if (new Set(rows.map((r) => r.symbol.trim())).size !== rows.length) errors.push("A benchmark symbol appears twice.");
    benchmark = {
      components: rows.map((r, i) => ({
        symbol: r.symbol.trim(),
        weight: String(Math.round(weights[i] * 1e6) / 1e8),
        currency: r.currency.trim().toUpperCase(),
        basis: r.basis,
      })),
      rebalance: form.rebalance,
    };
  }
  const splitRows = form.splits.filter((r) => r.ticker.trim() !== "" || r.day !== "" || r.ratio.trim() !== "");
  for (const r of splitRows) {
    const ratio = Number(r.ratio);
    if (r.ticker.trim() === "" || !/^\d{4}-\d{2}-\d{2}$/.test(r.day) || !Number.isFinite(ratio) || ratio <= 0 || ratio === 1) {
      errors.push("Every split needs a ticker, a date and a ratio above 0 other than 1 (2 for a 2-for-1, 0.1 for a 1-for-10 reverse split).");
      break;
    }
  }
  const splits = splitRows.map((r) => ({ ticker: r.ticker.trim(), day: r.day, ratio: String(Number(r.ratio)), note: r.note.trim() }));
  const isDay = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);
  const mergerRows = form.mergers.filter((r) => [r.old_ticker, r.new_ticker, r.day, r.ratio].some((v) => v.trim() !== ""));
  for (const r of mergerRows) {
    const ratio = Number(r.ratio);
    if (r.old_ticker.trim() === "" || r.new_ticker.trim() === "" || r.old_ticker.trim() === r.new_ticker.trim() || !isDay(r.day) || !Number.isFinite(ratio) || ratio <= 0) {
      errors.push("Every merger needs two different tickers, a date and the new shares you got for each old one (above 0).");
      break;
    }
  }
  const mergers = mergerRows.map((r) => ({
    old_ticker: r.old_ticker.trim(),
    new_ticker: r.new_ticker.trim(),
    day: r.day,
    ratio: String(Number(r.ratio)),
    note: r.note.trim(),
  }));
  const spinRows = form.spin_offs.filter((r) => [r.parent_ticker, r.child_ticker, r.day, r.ratio, r.cost_percent].some((v) => v.trim() !== ""));
  for (const r of spinRows) {
    const ratio = Number(r.ratio);
    const cost = Number(r.cost_percent);
    if (
      r.parent_ticker.trim() === "" || r.child_ticker.trim() === "" || r.parent_ticker.trim() === r.child_ticker.trim() ||
      !isDay(r.day) || !Number.isFinite(ratio) || ratio <= 0 || r.cost_percent.trim() === "" || !Number.isFinite(cost) || cost < 0 || cost > 100
    ) {
      errors.push("Every spin-off needs two different tickers, a date, the child shares per parent share (above 0) and the share of cost that moved (0 to 100).");
      break;
    }
  }
  const spin_offs = spinRows.map((r) => ({
    parent_ticker: r.parent_ticker.trim(),
    child_ticker: r.child_ticker.trim(),
    day: r.day,
    ratio: String(Number(r.ratio)),
    cost_share: String(Math.round(Number(r.cost_percent) * 1e4) / 1e6),
    note: r.note.trim(),
  }));
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    body: {
      core_tickers: tickers,
      cost_method: form.cost_method,
      time_test_years: years,
      time_test_warning_months: warn,
      exemption_limit: String(limit),
      exemption_currency: currency,
      sleeve_gap_points: String(gap),
      sleeve_lookback_years: lookback,
      cash_tolerance: String(tolerance),
      alert_severity: severity,
      benchmark,
      splits,
      mergers,
      spin_offs,
      note: form.note.trim(),
    },
  };
}
