/*
 * A small parser for the method documents in docs/methods/: headings, paragraphs, bullet lists,
 * pipe tables, fenced code and inline `code` / **bold**. Nothing else is supported on purpose, so
 * the documents stay plain and no HTML is ever injected.
 */

export type Inline = { kind: "text" | "code" | "bold"; text: string };

export type Block =
  | { kind: "h1" | "h2" | "h3"; text: string }
  | { kind: "p"; inline: Inline[] }
  | { kind: "ul"; items: Inline[][] }
  | { kind: "table"; head: Inline[][]; rows: Inline[][][] }
  | { kind: "code"; text: string };

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", text: text.slice(last, at) });
    const token = m[0];
    out.push(token.startsWith("`") ? { kind: "code", text: token.slice(1, -1) } : { kind: "bold", text: token.slice(2, -2) });
    last = at + token.length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => parseInline(c.trim()));

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: (["h1", "h2", "h3"] as const)[heading[1].length - 1], text: heading[2].trim() });
      i++;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const body = rows.filter((r, k) => !(k === 1 && /^\|?\s*:?-{3,}/.test(r.trim())));
      blocks.push({ kind: "table", head: cells(body[0] ?? ""), rows: body.slice(1).map(cells) });
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: Inline[][] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        let text = lines[i++].replace(/^[-*]\s+/, "");
        // A wrapped bullet continues on indented lines.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])) text += ` ${lines[i++].trim()}`;
        items.push(parseInline(text));
      }
      blocks.push({ kind: "ul", items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,3}\s|```|\||[-*]\s)/.test(lines[i])) para.push(lines[i++].trim());
    blocks.push({ kind: "p", inline: parseInline(para.join(" ")) });
  }
  return blocks;
}

/** `slug: description` bullets of docs/methods/index.md. */
export function parseIndex(source: string): { slug: string; description: string }[] {
  return source
    .split(/\r?\n/)
    .map((l) => /^[-*]\s+`?([a-z0-9_]+)`?\s*[:—-]\s*(.*)$/.exec(l.trim()))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => ({ slug: m[1], description: m[2].trim() }));
}
