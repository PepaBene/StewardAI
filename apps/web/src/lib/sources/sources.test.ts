import { describe, expect, it } from "vitest";

import { isExternalUrl } from "./sources";

describe("isExternalUrl", () => {
  it("allows only http and https", () => {
    expect(isExternalUrl("https://example.com/a?b=1")).toBe(true);
    expect(isExternalUrl("http://example.com")).toBe(true);
  });

  it("refuses script, data, file and relative links that a feed could carry", () => {
    expect(isExternalUrl("javascript:alert(1)")).toBe(false);
    expect(isExternalUrl("JaVaScRiPt:alert(1)")).toBe(false);
    expect(isExternalUrl("data:text/html,<script>1</script>")).toBe(false);
    expect(isExternalUrl("file:///etc/passwd")).toBe(false);
    expect(isExternalUrl("/news/stories/1")).toBe(false);
    expect(isExternalUrl("")).toBe(false);
    expect(isExternalUrl(null)).toBe(false);
    expect(isExternalUrl(undefined)).toBe(false);
  });
});
