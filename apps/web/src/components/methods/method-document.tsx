import type { ReactNode } from "react";

import type { Block, Inline } from "@/lib/methods/markdown";

function InlineText({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "code" ? (
          <code key={i} className="bg-surface-muted px-1 py-px font-mono text-[0.85em]">
            {p.text}
          </code>
        ) : p.kind === "bold" ? (
          <strong key={i} className="font-semibold">
            {p.text}
          </strong>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

/** A method document rendered from its parsed blocks; the first heading is the page's title. */
export function MethodDocument({ blocks }: { blocks: Block[] }) {
  const body: ReactNode[] = blocks.map((b, i) => {
    switch (b.kind) {
      case "h1":
        return null;
      case "h2":
        return (
          <h2 key={i} className="mt-8 border-t border-border-soft pt-4 text-lg font-semibold first:mt-0 first:border-0 first:pt-0">
            {b.text}
          </h2>
        );
      case "h3":
        return (
          <h3 key={i} className="mt-5 text-base font-semibold">
            {b.text}
          </h3>
        );
      case "p":
        return (
          <p key={i} className="mt-3 max-w-[72ch] text-sm leading-relaxed">
            <InlineText parts={b.inline} />
          </p>
        );
      case "ul":
        return (
          <ul key={i} className="mt-3 max-w-[72ch] list-disc space-y-1 pl-5 text-sm leading-relaxed">
            {b.items.map((item, k) => (
              <li key={k}>
                <InlineText parts={item} />
              </li>
            ))}
          </ul>
        );
      case "code":
        return (
          <pre key={i} className="mt-3 max-w-full overflow-x-auto border border-border bg-surface-muted px-3 py-2 font-mono text-xs leading-relaxed">
            {b.text}
          </pre>
        );
      case "table":
        return (
          <div key={i} className="mt-3 max-w-full overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface-muted text-left">
                  {b.head.map((h, k) => (
                    <th key={k} scope="col" className="px-3 py-1.5 font-medium">
                      <InlineText parts={h} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, k) => (
                  <tr key={k} className="border-t border-border-soft align-top">
                    {r.map((c, j) => (
                      <td key={j} className="px-3 py-1.5">
                        <InlineText parts={c} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
  return <article>{body}</article>;
}
