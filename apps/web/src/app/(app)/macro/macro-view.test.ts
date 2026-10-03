import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { MacroView } from "./macro-view";

describe("MacroView", () => {
  it("collects macro data only through the arm-then-confirm pattern (collapsed trigger, no confirm panel until armed)", () => {
    const html = renderToStaticMarkup(createElement(MacroView));
    expect(html).toContain("Collect macro data");
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*aria-controls=[^>]*>[^<]*(<[^>]*>)*Collect macro data/);
    expect(html).not.toContain("Collect macro data now?");
    expect(html).not.toContain("Queue job");
  });
  it("does not claim a holding has nothing mapped while its context is still loading", () => {
    const html = renderToStaticMarkup(createElement(MacroView));
    expect(html).not.toContain("has no mapped country or product");
  });
});
