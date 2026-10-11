import { describe, expect, it } from "vitest";

import {
  actionLabel,
  actionState,
  formatSeconds,
  formatUsd,
  kindLabel,
  noteSuggestionOf,
  parseTab,
  statementsOf,
  tabStatus,
} from "./inbox";

describe("inbox helpers", () => {
  it("names the event kinds in plain words and passes an unknown kind through", () => {
    expect(kindLabel("breach")).toBe("Portfolio breach");
    expect(kindLabel("verdict")).toBe("KPI breach");
    expect(kindLabel("something-new")).toBe("something-new");
  });

  it("reads a suggested note and what the owner did with it", () => {
    expect(noteSuggestionOf({ body: {} })).toBeNull();
    expect(noteSuggestionOf({ body: { suggested_note: "  " } })).toBeNull();
    expect(
      noteSuggestionOf({
        body: { suggested_note: "Watch margin.", note_status: "suggested" },
      }),
    ).toEqual({
      text: "Watch margin.",
      status: "suggested",
    });
    expect(
      noteSuggestionOf({
        body: { suggested_note: "Watch margin.", note_status: "accepted" },
      })?.status,
    ).toBe("accepted");
  });

  it("reads the tab from the URL and maps it to an API filter", () => {
    expect(parseTab(null)).toBe("new");
    expect(parseTab("bogus")).toBe("new");
    expect(parseTab("dismissed")).toBe("dismissed");
    expect(tabStatus("new")).toBe("new");
    expect(tabStatus("all")).toBeUndefined();
  });

  it("says what confirming will start", () => {
    expect(actionLabel(null)).toBeNull();
    expect(
      actionLabel({
        kind: "research",
        params: { ticker: "AAPL_US_EQ", profile: "deep-thesis" },
      }),
    ).toBe("Run a research report for AAPL_US_EQ (deep-thesis)");
    expect(actionLabel({ kind: "portfolio_sync", params: {} })).toBe(
      "Run the portfolio sync",
    );
  });

  it("tells an open action from a decided or missing one", () => {
    expect(actionState({ proposed_action: null, action_status: null })).toBe(
      "none",
    );
    expect(
      actionState({
        proposed_action: { kind: "research" },
        action_status: "proposed",
      }),
    ).toBe("open");
    expect(
      actionState({
        proposed_action: { kind: "research" },
        action_status: "confirmed",
      }),
    ).toBe("confirmed");
  });

  it("tolerates a body without statements and keeps only well-formed ones", () => {
    expect(statementsOf({ body: {} })).toEqual([]);
    expect(
      statementsOf({
        body: {
          statements: [{ text: "Cut.", source_ids: ["S1"] }, { nope: 1 }],
        },
      }),
    ).toEqual([{ text: "Cut.", source_ids: ["S1"] }]);
  });

  it("formats cost and time", () => {
    expect(formatUsd(null)).toBe("none");
    expect(formatUsd(0.004)).toBe("<$0.01");
    expect(formatUsd(2.5)).toBe("$2.50");
    expect(formatSeconds(42)).toBe("42 s");
    expect(formatSeconds(300)).toBe("5 min");
  });
});
