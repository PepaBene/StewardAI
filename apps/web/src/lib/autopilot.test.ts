import { describe, expect, it } from "vitest";

import {
  canUndo,
  confidenceLabel,
  deciderLabel,
  modeLabel,
  parseTab,
  precisionLabel,
  statusLabel,
  waitingLabel,
} from "./autopilot";

describe("autopilot helpers", () => {
  it("reads the tab from the URL and falls back to what needs the owner", () => {
    expect(parseTab(null)).toBe("needs-you");
    expect(parseTab("bogus")).toBe("needs-you");
    expect(parseTab("kinds")).toBe("kinds");
  });

  it("names modes and statuses in plain words", () => {
    expect(modeLabel("shadow")).toBe("Watching");
    expect(modeLabel("auto")).toBe("Acting");
    expect(statusLabel("escalated")).toBe("Waiting for you");
    expect(statusLabel("reverted")).toBe("Undone");
  });

  it("says who decided", () => {
    expect(deciderLabel("rule:listing-exact-match")).toBe(
      "Rule listing-exact-match",
    );
    expect(deciderLabel("model:gpt-6-luna")).toBe("gpt-6-luna");
    expect(deciderLabel("ttl")).toBe("Expired unanswered");
    expect(deciderLabel("owner-accepted")).toBe("You accepted");
  });

  it("formats confidence and precision, and admits when nothing was checked", () => {
    expect(confidenceLabel(0.934)).toBe("93%");
    expect(confidenceLabel(null)).toBe("—");
    expect(precisionLabel({ precision: null, reviewed: 0 })).toBe(
      "Not checked yet",
    );
    expect(precisionLabel({ precision: 0.96, reviewed: 52 })).toBe(
      "96% of 52 checked",
    );
  });

  it("offers undo only for an applied decision that can be reverted", () => {
    expect(canUndo({ status: "applied", revert_payload: { id: 1 } })).toBe(
      true,
    );
    expect(canUndo({ status: "applied", revert_payload: null })).toBe(false);
    expect(canUndo({ status: "shadow", revert_payload: null })).toBe(false);
  });

  it("words the wait", () => {
    expect(waitingLabel(0)).toBe("today");
    expect(waitingLabel(1)).toBe("1 day");
    expect(waitingLabel(6)).toBe("6 days");
  });
});
