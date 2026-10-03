import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChartFrame, ProvenanceFooter, Segmented } from "@/components/data-state";
import { placeholderState } from "@/lib/data-state/states";

const prov = (state: "verified" | "paid_only", sourceUrl?: string) => ({ source: "S", sourceUrl, asOf: null, method: "m", state, reason: null, coverage: null });

describe("data-state components", () => {
  it("F1-17: an empty frame under a verified state is a placeholder that does not say Verified", () => {
    expect(placeholderState("verified")).toBe("not_covered");
    expect(placeholderState("paid_only")).toBe("paid_only");
    const html = renderToStaticMarkup(createElement(ChartFrame, { title: "T", provenance: prov("verified"), hasValue: false } as never, "chart"));
    expect(html).not.toContain("Verified");
    expect(html).not.toContain("chart");
    expect(html).toContain("Not covered");
  });
  it("F7-10: the provenance source link is only rendered for http(s) URLs", () => {
    expect(renderToStaticMarkup(createElement(ProvenanceFooter, { provenance: prov("verified", "javascript:alert(1)") }))).not.toContain("href=");
    expect(renderToStaticMarkup(createElement(ProvenanceFooter, { provenance: prov("verified", "https://example.org/x") }))).toContain('href="https://example.org/x"');
  });
  it("F7-7: the segmented focus ring is drawn inside the button so a scroll container cannot clip it", () => {
    const html = renderToStaticMarkup(createElement(Segmented, { label: "L", options: [{ id: "a", label: "A" }], value: "a", onChange: () => {} }));
    expect(html).toContain("focus-visible:-outline-offset-2");
  });
  it("F7-3: the placeholder's next step is not drawn in the faint token", () => {
    const html = renderToStaticMarkup(createElement(ChartFrame, { title: "T", provenance: prov("paid_only"), hasValue: false } as never, "x"));
    expect(html).not.toContain("text-text-faint");
  });
});
