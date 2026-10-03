import { describe, expect, it } from "vitest";

import {
  alpha2FromTopologyId,
  choroplethBreaks,
  countryName,
  fitWorld,
  naturalEarth,
  projectGeometry,
  rampIndex,
  resolveGeography,
  splitRingAtAntimeridian,
} from "./geo";
import { MAP_HEIGHT, MAP_WIDTH, worldShapes } from "./geo-world";

describe("naturalEarth / fitWorld", () => {
  it("maps the origin to the origin and is odd in both axes", () => {
    expect(naturalEarth(0, 0)).toEqual([0, 0]);
    const [x, y] = naturalEarth(40, 30);
    const [nx, ny] = naturalEarth(-40, -30);
    expect(nx).toBeCloseTo(-x, 12);
    expect(ny).toBeCloseTo(-y, 12);
  });

  it("keeps the published Natural Earth I proportions (width about 1.9x height pole to pole)", () => {
    const w = naturalEarth(180, 0)[0] - naturalEarth(-180, 0)[0];
    const h = naturalEarth(0, 90)[1] - naturalEarth(0, -90)[1];
    expect(w / h).toBeGreaterThan(1.8);
    expect(w / h).toBeLessThan(2.1);
  });

  it("fits the world inside the frame, north up, with the origin at the centre", () => {
    const p = fitWorld(960, 480, 4);
    const [cx, cy] = p.project(0, 0);
    expect(cx).toBeCloseTo(480, 6);
    const [, north] = p.project(0, 80);
    const [, south] = p.project(0, -50);
    expect(north).toBeLessThan(cy);
    expect(south).toBeGreaterThan(cy);
    const [left] = p.project(-180, 0);
    const [right] = p.project(180, 0);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(right).toBeLessThanOrEqual(960);
    expect(p.project(0, 84)[1]).toBeGreaterThanOrEqual(0);
    expect(p.project(0, -58)[1]).toBeLessThanOrEqual(480);
  });
});

describe("resolveGeography", () => {
  it("accepts alpha-2 in any case", () => {
    expect(resolveGeography("DE")).toEqual({ kind: "country", code: "DE" });
    expect(resolveGeography(" us ")).toEqual({ kind: "country", code: "US" });
  });
  it("maps alpha-3 only where the backend table has it", () => {
    expect(resolveGeography("DEU")).toEqual({ kind: "country", code: "DE" });
    expect(resolveGeography("ZZZ")).toEqual({ kind: "unknown", code: null });
  });
  it("maps M49 numbers including the Comtrade aliases", () => {
    expect(resolveGeography("276")).toEqual({ kind: "country", code: "DE" });
    expect(resolveGeography("842")).toEqual({ kind: "country", code: "US" });
    expect(resolveGeography("840")).toEqual({ kind: "country", code: "US" });
    expect(resolveGeography("004")).toEqual({ kind: "country", code: "AF" });
    expect(resolveGeography(251)).toEqual({ kind: "country", code: "FR" });
    expect(resolveGeography("699")).toEqual({ kind: "country", code: "IN" });
  });
  it("keeps World and the EU aggregate apart from countries", () => {
    expect(resolveGeography("0")).toEqual({ kind: "world", code: null });
    expect(resolveGeography("W00")).toEqual({ kind: "world", code: null });
    expect(resolveGeography("97")).toEqual({ kind: "aggregate", code: "EU" });
    expect(resolveGeography("EU")).toEqual({ kind: "aggregate", code: "EU" });
  });
  it("never guesses", () => {
    for (const bad of ["", "  ", null, undefined, "XX", "9999", "A1", "EUROPE"]) {
      expect(resolveGeography(bad as string | null | undefined).kind).toBe("unknown");
    }
  });
});

describe("alpha2FromTopologyId / countryName", () => {
  it("handles the topology's zero-padded ids", () => {
    expect(alpha2FromTopologyId("004")).toBe("AF");
    expect(alpha2FromTopologyId("840")).toBe("US");
    expect(alpha2FromTopologyId(undefined)).toBeNull();
    expect(alpha2FromTopologyId("999")).toBeNull();
  });
  it("names countries and aggregates", () => {
    expect(countryName("DE")).toBe("Germany");
    expect(countryName("EU")).toBe("European Union");
    expect(countryName(null)).toBe("Unknown");
  });
});

describe("projectGeometry", () => {
  const identity = (lon: number, lat: number): [number, number] => [lon, lat];
  it("writes one subpath per ring and drops the closing duplicate", () => {
    const g = {
      type: "Polygon",
      coordinates: [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[2, 2], [4, 2], [4, 4], [2, 2]],
      ],
    };
    const s = projectGeometry(g, identity);
    expect(s.d).toBe("M0,0L10,0L10,10L0,10ZM2,2L4,2L4,4Z");
    expect(s.anchor).toEqual([5, 5]);
  });
  it("anchors a multipolygon on its largest ring", () => {
    const g = {
      type: "MultiPolygon",
      coordinates: [
        [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]],
      ],
    };
    expect(projectGeometry(g, identity).anchor).toEqual([15, 15]);
  });
  it("draws nothing for degenerate or non-polygon geometry", () => {
    expect(projectGeometry({ type: "Polygon", coordinates: [[[0, 0], [1, 1], [0, 0]]] }, identity).d).toBe("");
    expect(projectGeometry({ type: "Point", coordinates: [0, 0] }, identity).d).toBe("");
    expect(projectGeometry(null, identity).d).toBe("");
  });
});

describe("worldShapes", () => {
  const shapes = worldShapes();
  it("draws the 110m countries without Antarctica", () => {
    expect(shapes.length).toBeGreaterThan(170);
    expect(shapes.some((s) => s.name === "Antarctica")).toBe(false);
    expect(shapes.every((s) => s.d.startsWith("M"))).toBe(true);
  });
  it("maps the major economies to alpha-2 codes", () => {
    const byCode = new Map(shapes.map((s) => [s.code, s.name]));
    for (const code of ["US", "DE", "CN", "JP", "CZ", "FR", "NO", "GB", "IN", "BR", "RU"]) expect(byCode.has(code)).toBe(true);
    expect(byCode.get("DE")).toBe("Germany");
  });
  it("has at most one shape per code and leaves the unlisted areas without a code", () => {
    const codes = shapes.map((s) => s.code).filter((c): c is string => c !== null);
    expect(new Set(codes).size).toBe(codes.length);
    expect(shapes.filter((s) => s.code === null).length).toBeLessThan(8);
  });
  it("keeps every coordinate inside the frame and never wraps across the antimeridian", () => {
    for (const s of shapes) {
      for (const m of s.d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)) {
        const x = Number(m[1]);
        const y = Number(m[2]);
        expect(x).toBeGreaterThanOrEqual(-1);
        expect(x).toBeLessThanOrEqual(MAP_WIDTH + 1);
        expect(y).toBeGreaterThanOrEqual(-1);
        expect(y).toBeLessThanOrEqual(MAP_HEIGHT + 1);
      }
      expect(s.anchor).not.toBeNull();
    }
  });
  it("places countries where they belong (Germany east of the US, north of Brazil)", () => {
    const at = (c: string) => shapes.find((s) => s.code === c)!.anchor!;
    expect(at("DE")[0]).toBeGreaterThan(at("US")[0]);
    expect(at("DE")[1]).toBeLessThan(at("BR")[1]);
  });
});

describe("choropleth classes", () => {
  it("builds equal-interval edges ending on a round maximum", () => {
    const b = choroplethBreaks([0.01, 0.34, 0.12, null, 0])!;
    expect(b.edges).toHaveLength(6);
    expect(b.max).toBeGreaterThanOrEqual(0.34);
    expect(b.edges.every((e, i, a) => i === 0 || e > a[i - 1])).toBe(true);
  });
  it("has no classes when nothing is positive", () => {
    expect(choroplethBreaks([0, 0, null])).toBeNull();
    expect(choroplethBreaks([])).toBeNull();
  });
  it("keeps zero and missing apart from every class", () => {
    const b = choroplethBreaks([0.5, 0.1])!;
    expect(rampIndex(0, b)).toBeNull();
    expect(rampIndex(null, b)).toBeNull();
    expect(rampIndex(Number.NaN, b)).toBeNull();
  });
  it("gives the largest value the strongest slot and a small one the faintest", () => {
    const b = choroplethBreaks([0.6, 0.02])!;
    expect(rampIndex(0.6, b)).toBe(0);
    expect(rampIndex(0.02, b)).toBe(5);
  });
  it("puts a value exactly on an edge in the lower class", () => {
    const b = choroplethBreaks([0.6])!;
    expect(b.edges[0]).toBeCloseTo(0.1, 12);
    expect(rampIndex(b.edges[0], b)).toBe(5);
    expect(rampIndex(b.edges[0] + 1e-6, b)).toBe(4);
  });
  it("caps values above the last edge in the strongest class", () => {
    const b = choroplethBreaks([0.6])!;
    expect(rampIndex(5, b)).toBe(0);
  });
});

describe("antimeridian", () => {
  const proj = fitWorld(960, 480);
  const crossing = { type: "Polygon", coordinates: [[[170, 60], [-170, 60], [-170, 70], [170, 70], [170, 60]]] };
  it("splits a ring crossing +/-180 into rings that stay inside the world strip", () => {
    const parts = splitRingAtAntimeridian(crossing.coordinates[0]);
    expect(parts).toHaveLength(2);
    for (const r of parts) for (const [lon] of r) expect(Math.abs(lon)).toBeLessThanOrEqual(180 + 1e-9);
  });
  it("leaves a ring that does not cross unchanged", () => {
    const ring = [[0, 0], [10, 0], [10, 10], [0, 0]];
    expect(splitRingAtAntimeridian(ring)).toEqual([ring]);
  });
  it("draws no edge longer than half the map width for a crossing polygon", () => {
    const shape = projectGeometry(crossing, proj.project);
    expect(shape.d.split("M").length - 1).toBe(2);
    expect(shape.maxJump).toBeLessThan(480);
  });
  it("draws no edge longer than half the map width for any country", () => {
    for (const s of worldShapes()) {
      for (const sub of s.d.split("M").filter(Boolean)) {
        const xs = [...sub.matchAll(/(-?\d+(?:\.\d+)?),/g)].map((m) => Number(m[1]));
        for (let i = 1; i < xs.length; i++) expect(Math.abs(xs[i] - xs[i - 1])).toBeLessThan(MAP_WIDTH / 2);
      }
    }
  });
});
