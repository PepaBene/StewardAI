import type { Settings } from "./performance";

export type BenchmarkRow = { symbol: string; weight: string; currency: string; basis: "total" | "price" | "unknown" };

export type SettingsForm = {
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

export type FormResult = { ok: true; body: Settings } | { ok: false; errors: string[] };

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
    } else if (Math.abs(weights.reduce((a, b) => a + b, 0) - 100) > 0.01) {
      errors.push("Benchmark weights must add up to 100.");
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
      note: form.note.trim(),
    },
  };
}
