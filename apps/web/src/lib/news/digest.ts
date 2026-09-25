import type { DigestBody, DigestSentence, DigestSource } from "@/lib/api/client";

/*
 * Claim-level citations for a digest: each sentence links to the news items and facts it
 * was written from. Sources are numbered in the order they appear in the source list, so
 * the number in the text matches the card in the sidebar.
 */

export type CitationLink = {
  key: string;
  label: string;
  href: string;
  title: string;
  external: boolean;
  anchor?: string;
};

export function sourceNumbers(sources: DigestSource[]): Map<number, number> {
  return new Map(sources.map((s, i) => [s.news_item_id, i + 1]));
}

export function sourceAnchor(number: number): string {
  return `source-${number}`;
}

export function citationLinks(
  sentence: Pick<DigestSentence, "news_item_ids" | "fact_ids">,
  sources: DigestSource[],
  numbers: Map<number, number>,
): CitationLink[] {
  const byId = new Map(sources.map((s) => [s.news_item_id, s]));
  const links: CitationLink[] = [];
  for (const id of sentence.news_item_ids ?? []) {
    const source = byId.get(id);
    const number = numbers.get(id);
    if (!source || number === undefined) continue;
    links.push({
      key: `n${id}`,
      label: String(number),
      href: source.url,
      title: `${source.headline} (${source.outlet ?? "source"}, tier ${source.tier})`,
      external: true,
      anchor: sourceAnchor(number),
    });
  }
  for (const id of sentence.fact_ids ?? []) {
    links.push({
      key: `f${id}`,
      label: `fact ${id}`,
      href: `/evidence/${id}`,
      title: `Extracted figure #${id}: open its source snippet and archived page`,
      external: false,
    });
  }
  return links;
}

/** Every sentence of the digest (overview and per-holding summaries). */
export function allSentences(body: DigestBody): DigestSentence[] {
  return [...(body.overview ?? []), ...(body.sections ?? []).flatMap((s) => s.summary ?? [])];
}
