/*
 * Geometry for the choropleth (components/charts/choropleth.tsx): the Natural Earth I
 * projection, a GeoJSON-to-SVG path writer, the ISO 3166 / UN M49 code tables the backend
 * uses (steward/macro/geo.py: exposure geographies and macro series are ISO alpha-2, Comtrade
 * reporters and partners are M49 numbers), and the sequential class breaks. Pure functions, no
 * DOM and no topology import, so it stays light and unit-testable; the world topology is
 * loaded by the lazy map component only.
 */

import { isFiniteNumber } from "./scale";

// ---------------------------------------------------------------------------------------
// Code tables
// ---------------------------------------------------------------------------------------

/** ISO 3166-1 numeric (also the UN M49 code) -> alpha-2. Mirrors steward.macro.geo.ISO2_TO_NUMERIC. */
export const NUMERIC_TO_ALPHA2: Readonly<Record<string, string>> = {
  "4": "AF", "8": "AL", "12": "DZ", "16": "AS", "20": "AD", "24": "AO",
  "28": "AG", "31": "AZ", "32": "AR", "36": "AU", "40": "AT", "44": "BS",
  "48": "BH", "50": "BD", "51": "AM", "52": "BB", "56": "BE", "60": "BM",
  "64": "BT", "68": "BO", "70": "BA", "72": "BW", "76": "BR", "84": "BZ",
  "90": "SB", "92": "VG", "96": "BN", "100": "BG", "104": "MM", "108": "BI",
  "112": "BY", "116": "KH", "120": "CM", "124": "CA", "132": "CV", "136": "KY",
  "140": "CF", "144": "LK", "148": "TD", "152": "CL", "156": "CN", "158": "TW",
  "170": "CO", "174": "KM", "175": "YT", "178": "CG", "180": "CD", "184": "CK",
  "188": "CR", "191": "HR", "192": "CU", "196": "CY", "203": "CZ", "204": "BJ",
  "208": "DK", "212": "DM", "214": "DO", "218": "EC", "222": "SV", "226": "GQ",
  "231": "ET", "232": "ER", "233": "EE", "234": "FO", "238": "FK", "242": "FJ",
  "246": "FI", "250": "FR", "254": "GF", "258": "PF", "262": "DJ", "266": "GA",
  "268": "GE", "270": "GM", "275": "PS", "276": "DE", "288": "GH", "292": "GI",
  "296": "KI", "300": "GR", "304": "GL", "308": "GD", "312": "GP", "316": "GU",
  "320": "GT", "324": "GN", "328": "GY", "332": "HT", "340": "HN", "344": "HK",
  "348": "HU", "352": "IS", "356": "IN", "360": "ID", "364": "IR", "368": "IQ",
  "372": "IE", "376": "IL", "380": "IT", "384": "CI", "388": "JM", "392": "JP",
  "398": "KZ", "400": "JO", "404": "KE", "408": "KP", "410": "KR", "414": "KW",
  "417": "KG", "418": "LA", "422": "LB", "426": "LS", "428": "LV", "430": "LR",
  "434": "LY", "438": "LI", "440": "LT", "442": "LU", "446": "MO", "450": "MG",
  "454": "MW", "458": "MY", "462": "MV", "466": "ML", "470": "MT", "474": "MQ",
  "478": "MR", "480": "MU", "484": "MX", "492": "MC", "496": "MN", "498": "MD",
  "499": "ME", "500": "MS", "504": "MA", "508": "MZ", "512": "OM", "516": "NA",
  "520": "NR", "524": "NP", "528": "NL", "531": "CW", "533": "AW", "534": "SX",
  "540": "NC", "548": "VU", "554": "NZ", "558": "NI", "562": "NE", "566": "NG",
  "570": "NU", "574": "NF", "578": "NO", "580": "MP", "583": "FM", "584": "MH",
  "585": "PW", "586": "PK", "591": "PA", "598": "PG", "600": "PY", "604": "PE",
  "608": "PH", "612": "PN", "616": "PL", "620": "PT", "624": "GW", "626": "TL",
  "630": "PR", "634": "QA", "638": "RE", "642": "RO", "643": "RU", "646": "RW",
  "654": "SH", "659": "KN", "660": "AI", "662": "LC", "666": "PM", "670": "VC",
  "674": "SM", "678": "ST", "682": "SA", "686": "SN", "688": "RS", "690": "SC",
  "694": "SL", "702": "SG", "703": "SK", "704": "VN", "705": "SI", "706": "SO",
  "710": "ZA", "716": "ZW", "724": "ES", "728": "SS", "729": "SD", "740": "SR",
  "748": "SZ", "752": "SE", "756": "CH", "760": "SY", "762": "TJ", "764": "TH",
  "768": "TG", "776": "TO", "780": "TT", "784": "AE", "788": "TN", "792": "TR",
  "795": "TM", "796": "TC", "798": "TV", "800": "UG", "804": "UA", "807": "MK",
  "818": "EG", "826": "GB", "834": "TZ", "840": "US", "854": "BF", "858": "UY",
  "860": "UZ", "862": "VE", "876": "WF", "882": "WS", "887": "YE", "894": "ZM",
};

/** Comtrade's own numbers where they differ from the ISO numeric code (steward.macro.geo.COMTRADE_ALIASES). */
export const COMTRADE_ALIASES: Readonly<Record<string, string>> = {
  "251": "FR",
  "381": "IT",
  "579": "NO",
  "757": "CH",
  "842": "US",
  "699": "IN",
  "490": "TW",
};

/** ISO alpha-2 -> alpha-3 for the economies the backend pins by alpha-3 (steward.macro.geo.ISO2_TO_ISO3). */
export const ALPHA2_TO_ALPHA3: Readonly<Record<string, string>> = {
  AT: "AUT", AU: "AUS", BE: "BEL", BR: "BRA", CA: "CAN", CH: "CHE", CL: "CHL", CN: "CHN",
  CO: "COL", CR: "CRI", CZ: "CZE", DE: "DEU", DK: "DNK", EE: "EST", ES: "ESP", FI: "FIN",
  FR: "FRA", GB: "GBR", GR: "GRC", HU: "HUN", ID: "IDN", IE: "IRL", IL: "ISR", IN: "IND",
  IS: "ISL", IT: "ITA", JP: "JPN", KR: "KOR", LT: "LTU", LU: "LUX", LV: "LVA", MX: "MEX",
  NL: "NLD", NO: "NOR", NZ: "NZL", PL: "POL", PT: "PRT", RU: "RUS", SA: "SAU", SE: "SWE",
  SI: "SVN", SK: "SVK", TR: "TUR", US: "USA", ZA: "ZAF",
};

const ALPHA3_TO_ALPHA2: Readonly<Record<string, string>> = Object.fromEntries(Object.entries(ALPHA2_TO_ALPHA3).map(([a2, a3]) => [a3, a2]));
const ALPHA2_SET = new Set(Object.values(NUMERIC_TO_ALPHA2));

/** Not one country: a Comtrade reporter an exposure may be mapped to, with no polygon on the map. */
export const AGGREGATE_NAMES: Readonly<Record<string, string>> = { EU: "European Union" };

export type GeographyKind = "country" | "aggregate" | "world" | "unknown";

export type Geography = { kind: GeographyKind; /** ISO alpha-2 (or "EU"); null for world and unknown. */ code: string | null };

/**
 * Resolves what the backend calls a geography or a Comtrade reporter/partner (ISO alpha-2,
 * alpha-3, an M49 number, "EU", World) to an ISO alpha-2 code. Never guesses: an unrecognised
 * code is `unknown`, which the map lists as "not drawn" instead of dropping it.
 */
export function resolveGeography(input: string | number | null | undefined): Geography {
  if (input === null || input === undefined) return { kind: "unknown", code: null };
  const text = String(input).trim().toUpperCase();
  if (!text) return { kind: "unknown", code: null };
  if (text === "W00" || text === "WLD" || text === "WORLD") return { kind: "world", code: null };
  if (/^\d+$/.test(text)) {
    const n = String(Number.parseInt(text, 10));
    if (n === "0") return { kind: "world", code: null };
    if (n === "97") return { kind: "aggregate", code: "EU" };
    const code = COMTRADE_ALIASES[n] ?? NUMERIC_TO_ALPHA2[n];
    return code ? { kind: "country", code } : { kind: "unknown", code: null };
  }
  if (text in AGGREGATE_NAMES) return { kind: "aggregate", code: text };
  if (/^[A-Z]{2}$/.test(text)) return ALPHA2_SET.has(text) ? { kind: "country", code: text } : { kind: "unknown", code: null };
  if (/^[A-Z]{3}$/.test(text)) {
    const code = ALPHA3_TO_ALPHA2[text];
    return code ? { kind: "country", code } : { kind: "unknown", code: null };
  }
  return { kind: "unknown", code: null };
}

/** The alpha-2 code of a topology feature id ("004", 4, "840"), or null when it is not a listed country. */
export function alpha2FromTopologyId(id: string | number | undefined | null): string | null {
  if (id === undefined || id === null || id === "") return null;
  const n = Number.parseInt(String(id), 10);
  return Number.isFinite(n) ? (NUMERIC_TO_ALPHA2[String(n)] ?? null) : null;
}

/** English display name of a country or aggregate code ("DE" -> "Germany"); falls back to the code. */
export function countryName(code: string | null | undefined): string {
  if (!code) return "Unknown";
  if (code in AGGREGATE_NAMES) return AGGREGATE_NAMES[code];
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

// ---------------------------------------------------------------------------------------
// Projection
// ---------------------------------------------------------------------------------------

const RAD = Math.PI / 180;

/**
 * Natural Earth I (Šavrič, Jenny, Patterson et al. polynomial approximation). Longitude and
 * latitude in degrees -> planar x (right) and y (up) in arbitrary units.
 */
export function naturalEarth(lonDeg: number, latDeg: number): [number, number] {
  const lambda = lonDeg * RAD;
  const phi = latDeg * RAD;
  const p2 = phi * phi;
  const p4 = p2 * p2;
  const x = lambda * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)));
  const y = phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)));
  return [x, y];
}

/** Antarctica is left off: it spans the bottom of the frame and carries no exposure. */
export const MAP_LAT_RANGE: readonly [number, number] = [-58, 84];

export type Projection = {
  width: number;
  height: number;
  /** Longitude/latitude (degrees) -> SVG pixels (y down). */
  project: (lon: number, lat: number) => [number, number];
};

/** A projection fitted so the whole world (without Antarctica) fills `width` x `height` less `padding`. */
export function fitWorld(width: number, height: number, padding = 4): Projection {
  const [latMin, latMax] = MAP_LAT_RANGE;
  const xs = [naturalEarth(-180, 0)[0], naturalEarth(180, 0)[0]];
  const ys = [naturalEarth(0, latMin)[1], naturalEarth(0, latMax)[1]];
  const spanX = xs[1] - xs[0];
  const spanY = ys[1] - ys[0];
  const scale = Math.min((width - 2 * padding) / spanX, (height - 2 * padding) / spanY);
  const offsetX = (width - spanX * scale) / 2 - xs[0] * scale;
  const offsetY = (height - spanY * scale) / 2 + ys[1] * scale;
  return {
    width,
    height,
    project: (lon, lat) => {
      const [x, y] = naturalEarth(lon, lat);
      return [offsetX + x * scale, offsetY - y * scale];
    },
  };
}

// ---------------------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------------------

export type Ring = readonly (readonly number[])[];
export type PolygonCoords = readonly Ring[];

/** Polygon and MultiPolygon coordinates of a GeoJSON geometry; other geometry types have none. */
export function polygonsOf(geometry: { type: string; coordinates?: unknown } | null | undefined): PolygonCoords[] {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return [geometry.coordinates as PolygonCoords];
  if (geometry.type === "MultiPolygon") return geometry.coordinates as PolygonCoords[];
  return [];
}

const r1 = (n: number) => Math.round(n * 10) / 10;

/** Projected, rounded points of one ring with consecutive duplicates removed. */
export function projectRing(ring: Ring, project: Projection["project"]): [number, number][] {
  const out: [number, number][] = [];
  for (const pt of ring) {
    if (pt.length < 2 || !isFiniteNumber(pt[0]) || !isFiniteNumber(pt[1])) continue;
    const [x, y] = project(pt[0], pt[1]);
    const p: [number, number] = [r1(x), r1(y)];
    const last = out[out.length - 1];
    if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
  }
  return out;
}

export type ProjectedShape = {
  /** SVG path data ("M..L..Z" per ring); empty when every ring collapsed. */
  d: string;
  /** Anchor for a tooltip: the centre of the bounding box of the largest ring. */
  anchor: [number, number] | null;
  /** Widest horizontal jump between two consecutive points (a wrap-around guard). */
  maxJump: number;
};

export function projectGeometry(
  geometry: { type: string; coordinates?: unknown } | null | undefined,
  project: Projection["project"],
): ProjectedShape {
  const parts: string[] = [];
  let best: { area: number; anchor: [number, number] } | null = null;
  let maxJump = 0;
  for (const polygon of polygonsOf(geometry)) {
    for (const [ringIndex, ring] of polygon.entries()) {
      const pts = projectRing(ring, project);
      // A closed ring repeats its first point; fewer than 3 distinct points draw nothing.
      if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
      if (pts.length < 3) continue;
      parts.push(`M${pts.map((p) => `${p[0]},${p[1]}`).join("L")}Z`);
      for (let i = 1; i < pts.length; i++) maxJump = Math.max(maxJump, Math.abs(pts[i][0] - pts[i - 1][0]));
      if (ringIndex === 0) {
        const xs = pts.map((p) => p[0]);
        const ys = pts.map((p) => p[1]);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        const area = (x1 - x0) * (y1 - y0);
        if (!best || area > best.area) best = { area, anchor: [r1((x0 + x1) / 2), r1((y0 + y1) / 2)] };
      }
    }
  }
  return { d: parts.join(""), anchor: best ? best.anchor : null, maxJump };
}

// ---------------------------------------------------------------------------------------
// Sequential classes
// ---------------------------------------------------------------------------------------

/** Ramp slots: accent-ramp-1 is the strongest, accent-ramp-6 the faintest (in both themes). */
export const RAMP_BG = [
  "bg-accent-ramp-1",
  "bg-accent-ramp-2",
  "bg-accent-ramp-3",
  "bg-accent-ramp-4",
  "bg-accent-ramp-5",
  "bg-accent-ramp-6",
] as const;

export const RAMP_FILL = [
  "fill-accent-ramp-1",
  "fill-accent-ramp-2",
  "fill-accent-ramp-3",
  "fill-accent-ramp-4",
  "fill-accent-ramp-5",
  "fill-accent-ramp-6",
] as const;

export const RAMP_CLASSES = RAMP_BG.length;

export type ChoroplethBreaks = {
  /** Upper bound of each class, ascending; the last equals `max`. Class i holds (edges[i-1], edges[i]]. */
  edges: number[];
  max: number;
};

/**
 * Equal-interval class edges from 0 to a round maximum >= the largest value. Null when no
 * value is positive (there is nothing to grade: every mapped country is zero).
 */
export function choroplethBreaks(values: readonly (number | null | undefined)[], classes = RAMP_CLASSES): ChoroplethBreaks | null {
  const finite = values.filter(isFiniteNumber).filter((v) => v > 0);
  if (finite.length === 0) return null;
  const top = Math.max(...finite);
  const rough = top / classes;
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const unit = rough / power;
  const nice = [1, 2, 2.5, 5, 10].find((n) => unit <= n + 1e-9) ?? 10;
  const step = nice * power;
  const edges: number[] = [];
  for (let i = 1; i <= classes; i++) edges.push(Number.parseFloat((step * i).toPrecision(12)));
  return { edges, max: edges[edges.length - 1] };
}

/**
 * The ramp index (0 = strongest slot) for a value, or null for null/non-finite/zero: zero and
 * missing are drawn differently from any class, never as the faintest colour. A value above the
 * last edge falls in the strongest class.
 */
export function rampIndex(value: number | null | undefined, breaks: ChoroplethBreaks | null): number | null {
  if (!isFiniteNumber(value) || value <= 0 || !breaks) return null;
  const k = breaks.edges.findIndex((e) => value <= e + 1e-12);
  const cls = k === -1 ? breaks.edges.length - 1 : k; // 0 = lowest class
  return breaks.edges.length - 1 - cls;
}
