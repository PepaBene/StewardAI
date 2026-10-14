import { describe, expect, it } from "vitest";

import { isConfigSave } from "./config-saved";

describe("isConfigSave", () => {
  it("matches a save of a config domain", () => {
    expect(isConfigSave("PUT", "https://api.example.com/config/theses")).toBe(true);
    expect(isConfigSave("put", "https://api.example.com/config/news-sources")).toBe(true);
  });

  it("ignores reads, rollbacks and imports", () => {
    expect(isConfigSave("GET", "https://api.example.com/config/theses")).toBe(false);
    expect(isConfigSave("POST", "https://api.example.com/config/theses/versions/3/rollback")).toBe(false);
    expect(isConfigSave("PUT", "https://api.example.com/config/theses/versions/3")).toBe(false);
  });
});
