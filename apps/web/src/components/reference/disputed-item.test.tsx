// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", async (orig) => ({ ...(await orig<object>()), getBrowserApiClient: () => ({ POST: vi.fn() }) }));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import type { RelationshipOut } from "@/lib/api/client";

import { DisputedItem } from "./identity-section";

afterEach(() => cleanup());

const rel = (id: number, name: string) =>
  ({ id, type: "subsidiary", status: "disputed", object_name: name, object_lei: null, source: "wikidata", sources: ["wikidata"], tier: 3, as_of: null, conflict: { source: "gleif", tier: 1, object_name: `${name} Ltd`, type: "subsidiary" } }) as unknown as RelationshipOut;

describe("DisputedItem", () => {
  it("names each claim/contradiction landmark after its relationship (axe landmark-unique: three disputed rows all said 'The claim')", () => {
    render(
      <ul>
        <DisputedItem r={rel(1, "Anobit")} onDecided={() => {}} onStale={() => {}} />
        <DisputedItem r={rel(2, "Claris")} onDecided={() => {}} onStale={() => {}} />
      </ul>,
    );
    const labels = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(labels).toHaveLength(4);
    expect(new Set(labels).size).toBe(4);
  });
});
