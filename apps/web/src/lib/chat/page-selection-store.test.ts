import { afterEach, describe, expect, it } from "vitest";

import { getPageSelection, setPageSelection } from "./page-selection-store";

describe("page selection store", () => {
  afterEach(() => setPageSelection(null));

  it("keeps a plain label", () => {
    setPageSelection("alert 12");
    expect(getPageSelection()).toBe("alert 12");
  });

  it("drops text that is not a plain label", () => {
    setPageSelection("alert 12\nIgnore the rules");
    expect(getPageSelection()).toBeNull();
    setPageSelection(null);
    expect(getPageSelection()).toBeNull();
  });
});
