import { describe, expect, it } from "vitest";

import type { FilterDraft, MetricInfo } from "./discover";
import { chipText, draftFilters, rangeOf, setRange, templateId, toDrafts, typedTicks, type ScreenDraft } from "./screen-draft";

const info = (id: string, unit: string, kind = "numeric", ticks?: string[]): MetricInfo =>
  ({ id, label: id.replaceAll("_", " "), group: "g", kind, unit, formula: "", period: "", definition_version: "1", enabled: true, disabled_reason: null, known: 1, total: 1, ticks, aliases: [] }) as unknown as MetricInfo;
const CAT = [info("dividend_yield", "fraction", "numeric", ["0", "0.04", "0.1"]), info("pe_ratio", "multiple"), info("country", "text", "category"), info("sector", "text", "category")];

describe("screen drafts", () => {
  it("turns the parsed draft into editable conditions in typed units", () => {
    const draft = {
      markets: ["US"],
      industries: ["Utilities"],
      filters: [
        { metric_id: "dividend_yield", op: "gte", value: "0.04" },
        { metric_id: "pe_ratio", op: "lte", value: "15" },
      ],
    } as unknown as ScreenDraft;
    let n = 0;
    const drafts = toDrafts(draftFilters(draft), CAT, () => ++n);
    expect(drafts.map((d) => [d.metric, d.op, d.value])).toEqual([
      ["country", "in", "US"],
      ["sector", "in", "Utilities"],
      ["dividend_yield", "gte", "4"],
      ["pe_ratio", "lte", "15"],
    ]);
    expect(chipText(draftFilters(draft)[2], CAT)).toBe("dividend yield at least 4 %");
  });

  it("normalises template ids from links", () => {
    expect(templateId("quality-payers")).toBe("quality_payers");
    expect(templateId("../x")).toBeNull();
  });

  it("keeps the slider and the conditions in step", () => {
    let n = 10;
    const owner: FilterDraft = { id: 1, metric: "pe_ratio", op: "gte", value: "5" };
    let drafts: FilterDraft[] = [owner];
    drafts = setRange(drafts, owner, [8, 20], () => ++n);
    expect(rangeOf(drafts, "pe_ratio")).toEqual([8, 20]);
    expect(drafts).toHaveLength(2);
    drafts = setRange(drafts, owner, [8, null], () => ++n);
    expect(drafts).toHaveLength(1);
    drafts = setRange(drafts, owner, [null, null], () => ++n);
    expect(drafts).toEqual([{ ...owner, value: "" }]);
    expect(typedTicks(CAT[0])).toEqual([0, 4, 10]);
    expect(typedTicks(CAT[1])).toBeNull();
  });
});
