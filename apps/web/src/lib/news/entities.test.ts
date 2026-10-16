import { describe, expect, it } from "vitest";
import { decodeEntities } from "./entities";

describe("decodeEntities", () => {
  it("decodes named, decimal and hex entities", () => {
    expect(decodeEntities("Ben &amp; Jerry&#39;s &quot;pivot&quot;")).toBe('Ben & Jerry\'s "pivot"');
    expect(decodeEntities("Nvidia&#x2019;s rise &ndash; again")).toBe("Nvidia’s rise – again");
  });
  it("decodes once only", () => {
    expect(decodeEntities("&amp;lt;")).toBe("&lt;");
  });
  it("leaves unknown or malformed entities and plain text alone", () => {
    expect(decodeEntities("AT&T &foo; &#99999999; 5 < 6")).toBe("AT&T &foo; &#99999999; 5 < 6");
    expect(decodeEntities("")).toBe("");
  });
});
