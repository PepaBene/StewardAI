import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/*
 * The method documents live in the repository's docs/methods/ folder and are read when the pages
 * are built, so the page and the document can never disagree. The web app is built from apps/web,
 * two levels below the repository root.
 */

const DIR = path.join(process.cwd(), "..", "..", "docs", "methods");

export function methodSlugs(): string[] {
  if (!existsSync(DIR)) return [];
  return readdirSync(DIR)
    .filter((f) => f.endsWith(".md") && f !== "index.md")
    .map((f) => f.slice(0, -3))
    .filter((s) => /^[a-z0-9_]+$/.test(s))
    .sort();
}

export function readMethod(slug: string): string | null {
  if (!/^[a-z0-9_]+$/.test(slug)) return null;
  const file = path.join(DIR, `${slug}.md`);
  return existsSync(file) ? readFileSync(file, "utf-8") : null;
}
