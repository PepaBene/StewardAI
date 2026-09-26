import { describe, expect, it } from "vitest";

import { configSaveQuery } from "./use-config-editor";

describe("configSaveQuery", () => {
  it("sends the version the edits started from, for the API's 409 check", () => {
    expect(configSaveQuery("why", 7)).toEqual({ note: "why", base_version_id: 7 });
  });

  it("leaves it out when nothing was saved yet", () => {
    expect(configSaveQuery(null, null)).toEqual({ note: null });
  });
});
