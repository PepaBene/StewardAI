import { BarChart, Donut } from "@/components/charts";

/** A chart the assistant asked for. The server built it from stored records, so every value
 * has a source and a date; the model only chose which one to show. */
export type VisualBlock = {
  kind: string;
  chart: "bar" | "donut" | "none";
  title: string;
  unit: string;
  labels: string[];
  values: number[];
  source: string;
  as_of: string | null;
  summary: string;
  caption?: string | null;
  table: Record<string, unknown>[];
};

export function ChatVisual({ block }: { block: VisualBlock }) {
  const hasData = block.chart !== "none" && block.values.length > 0;
  const columns = block.table[0] ? Object.keys(block.table[0]) : [];
  return (
    <figure className="my-3 border border-border p-3">
      <figcaption className="mb-2 text-sm font-medium text-text">{block.title}</figcaption>
      {hasData && block.chart === "donut" ? (
        <Donut
          segments={block.labels.map((label, i) => ({ label, value: block.values[i] ?? 0 }))}
          label={block.title}
          monoLabels
          formatShare={(f) => `${(f * 100).toFixed(1)}%`}
        />
      ) : null}
      {hasData && block.chart === "bar" ? (
        <BarChart
          data={block.labels.map((label, i) => ({ label, value: block.values[i] ?? null }))}
          label={`${block.title}, ${block.unit}`}
          formatValue={(v) => String(Math.round(v * 100) / 100)}
        />
      ) : null}
      {!hasData ? <p className="text-sm text-text-muted">{block.caption ?? block.summary}</p> : null}
      <p className="mt-2 text-sm text-text-muted">{block.summary}</p>
      {hasData ? (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-text-muted">Show the numbers</summary>
          <table
            aria-label={block.summary}
            className="mt-2 w-full border-collapse text-left tabular-nums"
          >
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c} scope="col" className="border-b border-border py-1 pr-3 font-medium">
                    {c.replaceAll("_", " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.table.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c} className="py-1 pr-3">
                      {row[c] == null ? "—" : String(row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
      {block.source ? (
        <p className="mt-2 text-xs text-text-muted">
          Source: {block.source.replaceAll("_", " ")}
          {block.as_of ? `, as of ${block.as_of}` : ""}
        </p>
      ) : null}
    </figure>
  );
}
