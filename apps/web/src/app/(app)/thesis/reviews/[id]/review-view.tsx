"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type PortfolioReview } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

type Holding = { ticker: string; weight: number; action: string; reasons?: string[] };
type Gap = { dimension: string; bucket: string; target: number; actual: number; gap: number };
type Verdict = {
  ticker: string;
  memo_id?: number;
  reason: string;
  summary?: string;
  expected_return?: number;
  asymmetry?: number | null;
  price?: number;
};
type Body = { holdings?: Holding[]; gaps?: Gap[]; buys?: Verdict[]; passes?: Verdict[]; notes?: string[]; candidates?: string[] };

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

function Candidate({ v }: { v: Verdict }) {
  return (
    <li className="py-3">
      <span className="font-mono text-sm">{v.ticker}</span>
      {v.memo_id ? (
        <Link href={`/thesis/memos/${v.memo_id}`} className="ml-2 text-xs text-accent-ink hover:underline">
          read the thesis
        </Link>
      ) : null}
      <p className="mt-1 text-sm">{v.reason}</p>
      {v.expected_return !== undefined ? (
        <p className="num text-xs text-text-muted">
          Expected return {Math.round(v.expected_return * 100)}%
          {v.asymmetry ? ` · upside ${v.asymmetry}x the loss` : ""}
          {v.price ? ` · price ${v.price}` : ""}
        </p>
      ) : null}
      {v.summary ? <p className="mt-1 line-clamp-3 text-sm text-text-muted">{v.summary}</p> : null}
    </li>
  );
}

export function ReviewView({ id }: { id: number }) {
  const [review, setReview] = useState<PortfolioReview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    getBrowserApiClient()
      .GET("/thesis/reviews/{review_id}", { params: { path: { review_id: id } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setReview(data);
        else setError(`The API answered HTTP ${response.status}.`);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!id) return <AlertMessage title="No such review">The address has no review number.</AlertMessage>;
  if (error) return <AlertMessage title="Could not load the review">{error}</AlertMessage>;
  if (!review)
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading…
      </p>
    );

  const body = review.body as unknown as Body;
  const holdings = (body.holdings ?? []).filter((h) => h.action !== "no thesis");
  const missing = (body.holdings ?? []).filter((h) => h.action === "no thesis");

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        kicker="Portfolio review"
        title={`Review #${review.id}`}
        description={
          review.status === "done"
            ? `Finished ${review.finished_at ? formatDateTime(review.finished_at) : ""}. Nothing was traded; these are views to weigh.`
            : "Still researching the candidates. Reload in a few minutes."
        }
      />

      {review.summary ? (
        <Blueprint className="px-5 py-[18px]">
          <PanelHeading kicker="In short" title="What the review found" />
          <p className="mt-2 max-w-[70ch] text-[15px] leading-relaxed whitespace-pre-line">{review.summary}</p>
        </Blueprint>
      ) : null}

      <Blueprint className="px-5 py-[18px]">
        <PanelHeading kicker="From your holdings" title="Sell, trim or look again" />
        {holdings.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">No holding is over a limit, past its sell target, in breach or marked trim or exit.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border-soft">
            {holdings.map((h) => (
              <li key={h.ticker} className="py-3">
                <span className="font-mono text-sm">{h.ticker}</span>
                <span className="ml-2 bg-border-soft px-2 py-px text-xs font-medium">{h.action}</span>
                <span className="num ml-2 text-xs text-text-muted">{pct(h.weight)} of the portfolio</span>
                <ul className="mt-1 list-disc pl-5 text-sm">
                  {h.reasons?.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
        {missing.length > 0 ? (
          <p className="mt-3 text-xs text-text-muted">
            No thesis yet for {missing.map((m) => m.ticker).join(", ")}. <Link href="/thesis" className="text-accent-ink hover:underline">Write them</Link>.
          </p>
        ) : null}
      </Blueprint>

      <Blueprint className="px-5 py-[18px]">
        <PanelHeading kicker="From your strategy" title="Gaps" />
        {(body.gaps ?? []).length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">No category is below its target band.</p>
        ) : (
          <ul className="num mt-2 space-y-1 text-sm">
            {body.gaps?.map((g) => (
              <li key={`${g.dimension}-${g.bucket}`}>
                {g.dimension} {g.bucket}: {pct(g.actual)} against {pct(g.target)}
              </li>
            ))}
          </ul>
        )}
      </Blueprint>

      <Blueprint className="px-5 py-[18px]">
        <PanelHeading kicker="Researched in depth" title="Buy" />
        {(body.buys ?? []).length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">No candidate cleared your return hurdle and asymmetry gate.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border-soft">{body.buys?.map((v) => <Candidate key={v.ticker} v={v} />)}</ul>
        )}
      </Blueprint>

      {(body.passes ?? []).length > 0 ? (
        <Blueprint className="px-5 py-[18px]">
          <PanelHeading kicker="Researched and rejected" title="Pass" />
          <ul className="mt-2 divide-y divide-border-soft">{body.passes?.map((v) => <Candidate key={v.ticker} v={v} />)}</ul>
        </Blueprint>
      ) : null}

      {(body.notes ?? []).length > 0 ? (
        <ul className="list-disc pl-5 text-xs text-text-muted">
          {body.notes?.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
