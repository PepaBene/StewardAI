import { describe, expect, it } from "vitest";

import type { ActivityCoverage, AlertOut } from "@/lib/api/client";

import {
  alertEventLabel,
  alertHeadline,
  alertOutcome,
  coverageRows,
  describeCorrected,
  EMPTY_JOB_FILTERS,
  evalTarget,
  formatScore,
  isJobFiltered,
  jobFiltersToSearch,
  jobLogQuery,
  jobTypeOptions,
  paramsSummary,
  parseJobFilters,
  runDuration,
  sortTrust,
} from "./activity";

const L = "en-US";

describe("job log filters", () => {
  it("round-trips the filters through the URL, dropping junk", () => {
    const f = parseJobFilters(new URLSearchParams("type=news_ingest&type=daily_digest&type=DROP%20TABLE&status=failed&status=nope&ticker=AAPL_US_EQ"));
    expect(f).toEqual({ types: ["news_ingest", "daily_digest"], statuses: ["failed"], ticker: "AAPL_US_EQ" });
    expect(jobFiltersToSearch(f)).toBe("?type=news_ingest&type=daily_digest&status=failed&ticker=AAPL_US_EQ");
    expect(parseJobFilters(new URLSearchParams(jobFiltersToSearch(f).slice(1)))).toEqual(f);
    expect(jobFiltersToSearch(EMPTY_JOB_FILTERS)).toBe("");
    expect(parseJobFilters(new URLSearchParams("ticker=bad ticker!")).ticker).toBeNull();
    expect(isJobFiltered(EMPTY_JOB_FILTERS)).toBe(false);
    expect(isJobFiltered(f)).toBe(true);
  });

  it("keeps other URL parameters", () => {
    expect(jobFiltersToSearch({ ...EMPTY_JOB_FILTERS, statuses: ["failed"] }, new URLSearchParams("alerts=all&status=queued"))).toBe(
      "?alerts=all&status=failed",
    );
  });

  it("builds the GET /jobs query", () => {
    expect(jobLogQuery(EMPTY_JOB_FILTERS, null)).toEqual({ job_type: undefined, status: undefined, ticker: undefined, before_id: undefined, limit: 50 });
    expect(jobLogQuery({ types: ["monitor"], statuses: ["failed", "running"], ticker: "X" }, 90, 20)).toEqual({
      job_type: ["monitor"],
      status: ["failed", "running"],
      ticker: "X",
      before_id: 90,
      limit: 20,
    });
  });

  it("lists every known job type, plus unknown ones seen in the log", () => {
    const options = jobTypeOptions(["brand_new_job"]);
    expect(options.map((o) => o.value)).toEqual(expect.arrayContaining(["news_ingest", "event_escalation", "monitor", "brand_new_job"]));
    expect(options.find((o) => o.value === "news_ingest")?.label).toBe("News check");
    expect(options.find((o) => o.value === "brand_new_job")?.label).toBe("Brand new job");
  });

  it("describes a run's parameters and duration", () => {
    expect(paramsSummary({ ticker: "AAPL_US_EQ", kind: "daily", nested: { a: 1 } })).toBe("ticker AAPL_US_EQ · kind daily");
    expect(paramsSummary({})).toBeNull();
    const now = new Date("2026-09-25T12:00:00Z");
    expect(runDuration({ started_at: "2026-09-25T11:59:00Z", finished_at: "2026-09-25T11:59:12Z", status: "succeeded" }, now)).toBe("12 s");
    expect(runDuration({ started_at: "2026-09-25T11:58:00Z", finished_at: null, status: "running" }, now)).toBe("2 min");
    expect(runDuration({ started_at: null, finished_at: null, status: "queued" }, now)).toBe("—");
  });
});

describe("coverage", () => {
  const coverage: ActivityCoverage = {
    checked_at: "2026-09-25T12:00:00Z",
    news_window_hours: 48,
    gaps: 1,
    holdings: [
      {
        ticker: "AAPL_US_EQ",
        name: "Apple",
        held_since: "2026-01-01T00:00:00Z",
        news_last_covered: "2026-09-25T10:00:00Z",
        news_gap: false,
        earnings_last_check: "2026-09-20T08:00:00Z",
        earnings_last_status: "succeeded",
        price_symbol: "aapl.us",
        prices_last_date: "2026-09-24",
        prices_gap: false,
      },
      {
        ticker: "CEZ_CZ_EQ",
        name: null,
        held_since: "2026-01-01T00:00:00Z",
        news_last_covered: "2026-09-22T09:00:00Z",
        news_gap: true,
        earnings_last_check: null,
        earnings_last_status: null,
        price_symbol: "cez.cz",
        prices_last_date: "2026-09-10",
        prices_gap: true,
      },
      {
        ticker: "NEW_EQ",
        name: "New",
        held_since: "2026-09-25T00:00:00Z",
        news_last_covered: null,
        news_gap: false,
        earnings_last_check: "2026-09-24T08:00:00Z",
        earnings_last_status: "failed",
        price_symbol: null,
        prices_last_date: null,
        prices_gap: false,
      },
    ],
  };

  it("puts holdings with gaps first and spells each gap out", () => {
    const rows = coverageRows(coverage, L);
    expect(rows.map((r) => r.ticker)).toEqual(["CEZ_CZ_EQ", "AAPL_US_EQ", "NEW_EQ"]);
    const [cez, aapl, fresh] = rows;
    // A holding no news run has covered for 48 hours shows a visible gap, in words.
    expect(cez.news).toMatchObject({ gap: true, label: "Gap" });
    expect(cez.news.detail).toMatch(/^No news run has covered it for 2 days \(48 h\): last covered .+ \(3 days ago\)\.$/);
    expect(cez.prices).toMatchObject({ gap: true, label: "Gap" });
    expect(cez.prices.detail).toMatch(/more than 4 days old/);
    expect(cez.name).toBe("CEZ_CZ_EQ");
    expect(cez.gaps).toBe(2);
    expect(aapl.news).toMatchObject({ gap: false, label: "2 h ago" });
    expect(aapl.earnings.label).toBe("Checked 5 days ago");
    expect(fresh.news).toMatchObject({ gap: false, label: "Not yet" });
    expect(fresh.prices).toMatchObject({ gap: false, label: "No symbol" });
    expect(fresh.earnings).toMatchObject({ gap: true, label: "Failed 28 h ago" });
  });

  it("says when nothing ever covered a gap holding", () => {
    const [row] = coverageRows({ ...coverage, holdings: [{ ...coverage.holdings[1], news_last_covered: null, prices_last_date: null }] });
    expect(row.news.detail).toMatch(/^No news run has covered it within 2 days/);
    expect(row.prices.detail).toBe("No price bars for cez.cz.");
  });
});

describe("alerts", () => {
  const alert: AlertOut = {
    id: 1,
    kind: "system",
    severity: 2,
    ticker: "CEZ_CZ_EQ",
    event_type: "coverage_gap",
    cluster_id: null,
    verdict_id: null,
    channel: "telegram",
    message: "[Severity 2] StewardAI monitoring: CEZ_CZ_EQ has had no news coverage for 49 h\nwindow 48 h",
    status: "suppressed",
    send_after: "2026-09-25T07:00:00Z",
    sent_at: null,
    suppressed_reason: "daily cap reached",
    error: null,
    created_at: "2026-09-25T06:00:00Z",
  };

  it("names monitoring events and outcomes", () => {
    expect(alertEventLabel(alert)).toBe("Coverage gap");
    expect(alertEventLabel({ kind: "system", event_type: "missed_run" })).toBe("Missed run");
    expect(alertEventLabel({ kind: "system", event_type: "something_new" })).toBe("something new");
    expect(alertEventLabel({ kind: "news", event_type: "m_and_a" })).toBe("M&A");
    expect(alertEventLabel({ kind: "thesis_breach", event_type: null })).toBe("Thesis breach");
    expect(alertHeadline(alert.message)).toBe("CEZ_CZ_EQ has had no news coverage for 49 h");
    expect(alertOutcome(alert)).toBe("Suppressed: daily cap reached");
    expect(alertOutcome({ ...alert, status: "failed", error: "timeout" })).toBe("Failed: timeout");
    expect(alertOutcome({ ...alert, status: "pending" }, L)).toMatch(/^Held until /);
    expect(alertOutcome({ ...alert, status: "sent", sent_at: "2026-09-25T07:01:00Z" }, L)).toMatch(/^Sent .*2026/);
  });
});

describe("trust and eval candidates", () => {
  it("lists the least trusted sources first", () => {
    const row = { wrong_count: 0, useful_count: 0, demoted: false, updated_at: "2026-09-25T00:00:00Z" };
    expect(sortTrust([{ ...row, host: "b.org", score: 0.9 }, { ...row, host: "a.org", score: 0.4 }]).map((r) => r.host)).toEqual(["a.org", "b.org"]);
    expect(formatScore(0.6000001)).toBe("0.60");
  });

  it("describes what was corrected", () => {
    expect(describeCorrected({ kind: "earnings_figure", corrected: { value: "94036", value_as_written: "94,036", snippet: null } })).toBe(
      "value 94036, as printed “94,036”",
    );
    expect(describeCorrected({ kind: "news_label", corrected: { event_type: "dividend_cut", affected_ticker: "X", relevant: true, rumour: true } })).toBe(
      "Dividend cut · affects X · relevant · rumour",
    );
    expect(describeCorrected({ kind: "news_label", corrected: { event_type: "other", affected_ticker: null, relevant: false, rumour: false } })).toBe(
      "Other · affects no holding · not relevant",
    );
    expect(evalTarget({ fact_id: 12, news_item_id: null })).toEqual({ label: "Figure #12", href: "/evidence/12" });
    expect(evalTarget({ fact_id: null, news_item_id: 5 })).toEqual({ label: "News item #5", href: null });
  });
});
