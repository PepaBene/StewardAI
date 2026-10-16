// Copies the method documents from the repository's docs/methods/ (the source of truth, also read
// by the backend's golden tests) into apps/web/content/methods/, which is committed. The pages read
// the copy, so a deploy whose root directory is apps/web (files outside it not available) still
// builds with the documents. Runs before `next build`; does nothing when the source is absent.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(here, "..", "..", "..", "docs", "methods");
const target = path.resolve(here, "..", "content", "methods");

if (existsSync(source)) {
  mkdirSync(target, { recursive: true });
  for (const f of readdirSync(source).filter((n) => n.endsWith(".md"))) {
    copyFileSync(path.join(source, f), path.join(target, f));
  }
  console.log(`sync-methods: copied method documents to ${target}`);
} else {
  console.log("sync-methods: docs/methods not found next to apps/web; using the committed copy");
}
