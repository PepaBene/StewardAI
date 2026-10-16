import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/*
 * The method documents are written in the repository's docs/methods/ folder and copied into
 * apps/web/content/methods/ by scripts/sync-methods.mjs (run before every build), so the pages
 * are built from files inside the web app's own root directory: a deploy that only ships apps/web
 * still has them. The repository folder is the fallback when the copy is missing (a dev checkout
 * that has not run the sync). The pages are built ahead of time, so these reads happen at build.
 */

const CANDIDATES = [
  path.join(process.cwd(), "content", "methods"),
  path.join(process.cwd(), "..", "..", "docs", "methods"),
];

function methodsDir(): string | null {
  return CANDIDATES.find((d) => existsSync(d)) ?? null;
}

export function methodSlugs(): string[] {
  const dir = methodsDir();
  if (!dir) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".md") && f !== "index.md")
    .map((f) => f.slice(0, -3))
    .filter((s) => /^[a-z0-9_]+$/.test(s))
    .sort();
}

export function readMethod(slug: string): string | null {
  if (!/^[a-z0-9_]+$/.test(slug)) return null;
  const dir = methodsDir();
  if (!dir) return null;
  const file = path.join(dir, `${slug}.md`);
  return existsSync(file) ? readFileSync(file, "utf-8") : null;
}
