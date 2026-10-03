/*
 * The world's countries as projected SVG shapes, built once from the Natural Earth 110m
 * topology in `world-atlas`. This module imports the topology (about 100 kB), so only the lazy
 * map component loads it; geo.ts stays light.
 */

import type { GeometryCollection, Topology } from "topojson-specification";
import { feature } from "topojson-client";
import worldTopology from "world-atlas/countries-110m.json";

import { alpha2FromTopologyId, fitWorld, projectGeometry } from "./geo";

export const MAP_WIDTH = 960;
export const MAP_HEIGHT = 480;
/** Antarctica (ISO numeric 010). */
const OMITTED_IDS = new Set([10]);

export type CountryShape = {
  /** Topology feature id as published ("840"); null for the few disputed areas without one. */
  id: string | null;
  /** ISO alpha-2 when the backend's code table knows the country, otherwise null (drawn as no data). */
  code: string | null;
  name: string;
  d: string;
  anchor: [number, number] | null;
};

type CountryProps = { name: string };

let cache: CountryShape[] | null = null;

export function worldShapes(): CountryShape[] {
  if (cache) return cache;
  const topology = worldTopology as unknown as Topology<{ countries: GeometryCollection<CountryProps> }>;
  const projection = fitWorld(MAP_WIDTH, MAP_HEIGHT);
  const collection = feature(topology, topology.objects.countries);
  const shapes: CountryShape[] = [];
  for (const f of collection.features) {
    const id = f.id === undefined ? null : String(f.id);
    if (id !== null && OMITTED_IDS.has(Number.parseInt(id, 10))) continue;
    const shape = projectGeometry(f.geometry, projection.project);
    if (!shape.d) continue;
    shapes.push({ id, code: alpha2FromTopologyId(id), name: f.properties?.name ?? "Unnamed area", d: shape.d, anchor: shape.anchor });
  }
  cache = shapes;
  return shapes;
}
