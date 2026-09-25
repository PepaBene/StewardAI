#!/usr/bin/env node
/*
 * Fails if any file under src/ (other than the token file) contains a colour
 * literal or a Tailwind palette colour class. All colours live in
 * src/app/tokens.css and are used through token classes (bg-surface, text-gain...).
 *
 * Usage: node scripts/check-colors.mjs  (npm run lint:colors)
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "src");
export const TOKEN_FILE = join("src", "app", "tokens.css");
const EXTENSIONS = /\.(?:[cm]?[jt]sx?|css|scss|html|svg|md|mdx)$/;

const PALETTE =
  "slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|mauve|olive|mist|taupe";
const UTILITIES =
  "bg|text|border|border-[trblxyse]|ring|ring-offset|outline|fill|stroke|from|via|to|divide|placeholder|caret|accent|decoration|shadow|inset-shadow|drop-shadow|text-shadow";

export const RULES = [
  { name: "hex colour", re: /(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g },
  { name: "colour function", re: /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi },
  {
    name: "Tailwind palette class",
    re: new RegExp(`(?<![\\w-])(?:${UTILITIES})-(?:${PALETTE})-(?:50|[1-9]00|950)\\b`, "g"),
  },
  {
    name: "Tailwind black/white class",
    re: new RegExp(`(?<![\\w-])(?:${UTILITIES})-(?:black|white)\\b`, "g"),
  },
];

/** Returns violations found in `text` as { line, column, rule, match }. */
export function findColorViolations(text) {
  const violations = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const rule of RULES) {
      rule.re.lastIndex = 0;
      for (const m of line.matchAll(rule.re)) {
        violations.push({ line: i + 1, column: m.index + 1, rule: rule.name, match: m[0] });
      }
    }
  });
  return violations;
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (EXTENSIONS.test(entry)) out.push(full);
  }
  return out;
}

/** Scans src/ (excluding the token file). Returns [{ file, line, column, rule, match }]. */
export function scanSource(srcDir = SRC) {
  const results = [];
  for (const file of walk(srcDir)) {
    const rel = relative(ROOT, file).split(sep).join("/");
    if (rel === TOKEN_FILE.split(sep).join("/")) continue;
    for (const v of findColorViolations(readFileSync(file, "utf8"))) {
      results.push({ file: rel, ...v });
    }
  }
  return results;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const violations = scanSource();
  if (violations.length) {
    console.error(`Colour literals / palette classes outside ${TOKEN_FILE}:`);
    for (const v of violations) {
      console.error(`  ${v.file}:${v.line}:${v.column}  ${v.rule}: ${v.match}`);
    }
    console.error("Use a token from src/app/tokens.css instead.");
    process.exit(1);
  }
  console.log(`lint:colors OK: no colour literals outside ${TOKEN_FILE}`);
}
