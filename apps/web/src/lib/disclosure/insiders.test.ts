import { describe, expect, it } from "vitest";

import { SAMPLE_INSIDER_ROWS, sampleInsiders } from "@/lib/disclosure/fixtures-insiders";
import { CLASS_META, classOf, classesPresent, insidersKnown, netBars, netFromTransactions, stripEvents } from "@/lib/disclosure/insiders";

const T = (day: string) => Date.parse(`${day}T23:59:59Z`);
const END = T("2026-06-30");

describe("insider net", () => {
  const rows = netFromTransactions(SAMPLE_INSIDER_ROWS, END);
  const row = (role: string, window: number) => rows.find((r) => r.role === role && r.window === window)!;

  it("counts only discretionary, countable open-market trades", () => {
    const all = row("all", 90);
    expect(all.trades).toBe(3); // three purchases in June; the plan sale, bundled sale, award, gift and unclassified row are out
    expect(all.buyValue).toBeCloseTo(20000 * 18.5 + 8000 * 18.9 + 5000 * 19.2, 5);
    expect(all.sellValue).toBe(0);
  });

  it("keeps plan trades and bundled sales out of the net but visible as counts", () => {
    const all = row("all", 90);
    expect(all.planExcluded).toBe(1);
    expect(all.bundled).toBe(1);
  });

  it("counts people once and splits by role", () => {
    expect(row("all", 90).insiders).toBe(3);
    expect(row("officer", 90).insiders).toBe(1);
    expect(row("director", 90).insiders).toBe(2);
  });

  it("uses transaction date for the window and filing date for visibility", () => {
    expect(row("all", 30).trades).toBe(3);
    // 365 days adds the unplanned ten-percent-owner sale from February
    expect(row("all", 365).sellValue).toBe(100000 * 17.2);
    expect(row("all", 365).planUnknown).toBe(1);
    // a purchase dated 14 July is not known on 30 June
    expect(insidersKnown(SAMPLE_INSIDER_ROWS, END).some((t) => t.tx_date === "2026-07-14")).toBe(false);
    expect(netFromTransactions(SAMPLE_INSIDER_ROWS, T("2026-07-20")).find((r) => r.role === "director" && r.window === 30)!.trades).toBe(1);
  });

  it("returns nothing before anything was filed", () => {
    expect(netFromTransactions(SAMPLE_INSIDER_ROWS, T("2026-01-01")).every((r) => r.trades === 0)).toBe(true);
  });
});

describe("insider display", () => {
  it("gives each class a distinct mark and fill", () => {
    const shown = ["open_market_buy", "open_market_sell", "grant_award", "option_exercise", "tax_withholding", "gift"] as const;
    const keys = shown.map((c) => `${CLASS_META[c].mark}:${CLASS_META[c].filled}`);
    expect(new Set(keys).size).toBe(shown.length);
  });

  it("maps an unknown class to not classified", () => {
    expect(classOf({ economic_class: "mystery" })).toBe("unclassified");
  });

  it("a role with no discretionary trades has no bar and says so, never a zero net", () => {
    const bars = netBars(netFromTransactions(SAMPLE_INSIDER_ROWS, END), 90);
    expect(bars.map((b) => b.label)).toEqual(["Officers", "Directors", "10% owners (no trades)", "Other insiders (no trades)"]);
    expect(bars.find((b) => b.label.startsWith("10% owners"))!.value).toBeNull();
    expect(bars.find((b) => b.label === "Officers")!.value).toBeGreaterThan(0);
  });

  it("places events on one axis with the filing after the trade", () => {
    const { events } = stripEvents(sampleInsiders(END).transactions, END);
    expect(events.length).toBeGreaterThan(5);
    expect(events.every((e) => e.xFiled >= e.x)).toBe(true);
    expect(events.every((e) => e.x >= 0 && e.xFiled <= 1)).toBe(true);
    expect(classesPresent(sampleInsiders(END).transactions)).toContain("unclassified");
  });
});
