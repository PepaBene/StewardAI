import { formatPct } from "@/lib/charts/format";
import { heatLevel } from "@/lib/charts/heat";
import { isFiniteNumber } from "@/lib/charts/scale";
import { squarify } from "@/lib/charts/squarify";
import { cn } from "@/lib/utils";

import { HEAT_BG, HEAT_TEXT, HeatScale } from "./heat";
import { ChartEmpty } from "./shared";

export type TreemapItem = {
  id: string;
  /** Tile title, e.g. the ticker. */
  label: string;
  /** Size (e.g. portfolio weight as a fraction). Non-positive -> no tile. */
  weight: number;
  /** Colour value (e.g. today's move as a fraction); null -> neutral tile showing "—". */
  change: number | null;
  /** Optional link for the tile. */
  href?: string;
};

export type TreemapProps = {
  items: readonly TreemapItem[];
  /** |change| at which colour saturates. Default 0.03 (±3% daily move). */
  cap?: number;
  /** Width / height of the whole map. Default 2.2. */
  aspectRatio?: number;
  /** Show the scale key above the map. Default true. */
  scale?: boolean;
  formatWeight?: (weight: number) => string;
  formatChange?: (change: number) => string;
  /** Accessible name, e.g. "Daily move, sized by weight". */
  label?: string;
  className?: string;
};

const W = 1000;

/**
 * Squarified treemap: tile area ∝ weight, colour = change on the diverging heat scale. Tiles
 * are separated by a 2px gap; text is dropped from tiles too small to hold it (the tile's
 * native tooltip and the accessible list still carry every value).
 */
export function Treemap({
  items,
  cap = 0.03,
  aspectRatio = 2.2,
  scale = true,
  formatWeight = (w) => formatPct(w, 1),
  formatChange = (c) => formatPct(c, 1, true),
  label = "Treemap",
  className,
}: TreemapProps) {
  const H = W / aspectRatio;
  const tiles = squarify(items, (i) => i.weight, { x: 0, y: 0, w: W, h: H });
  if (tiles.length === 0) return <ChartEmpty minHeight={160} className={className} />;
  const totalWeight = tiles.reduce((s, t) => s + t.weight, 0);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {scale ? <HeatScale cap={cap} className="self-end" /> : null}
      <ul aria-label={label} className="relative m-0 w-full list-none p-0" style={{ aspectRatio }}>
        {tiles.map((t) => {
          const item = t.item;
          const level = heatLevel(item.change, cap);
          const share = t.weight / totalWeight;
          const wPct = (t.w / W) * 100;
          const hPct = (t.h / H) * 100;
          const roomy = wPct > 9 && hPct > 22;
          const fits = wPct > 5 && hPct > 12;
          const big = share > 0.1;
          const text = level ? HEAT_TEXT[level] : "text-text-muted";
          const changeText = isFiniteNumber(item.change) ? formatChange(item.change) : "—";
          const title = `${item.label} · weight ${formatWeight(item.weight)} · ${changeText}`;
          const Inner = item.href ? "a" : "div";
          return (
            <li
              key={item.id}
              className="absolute"
              style={{ left: `${(t.x / W) * 100}%`, top: `${(t.y / H) * 100}%`, width: `${wPct}%`, height: `${hPct}%` }}
            >
              <Inner
                {...(item.href ? { href: item.href } : {})}
                title={title}
                className={cn(
                  "absolute inset-px flex flex-col justify-between overflow-hidden p-2 no-underline",
                  level ? HEAT_BG[level] : "bg-surface-muted",
                  text,
                  item.href && "hover:outline-2 hover:outline-text focus-visible:outline-2",
                )}
              >
                <span className="sr-only">{title}</span>
                {fits ? (
                  <span aria-hidden="true" className="flex items-start justify-between gap-1 text-[11px] leading-none">
                    <span className="truncate font-mono font-semibold">{item.label}</span>
                    {roomy ? <span className="num shrink-0">{formatWeight(item.weight)}</span> : null}
                  </span>
                ) : null}
                {roomy ? (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "num font-heading leading-none font-semibold",
                      big ? "text-[30px]" : "text-[22px]",
                    )}
                  >
                    {changeText}
                  </span>
                ) : null}
              </Inner>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
