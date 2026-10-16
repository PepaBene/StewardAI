import { readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";

import type { NextConfig } from "next";

import { REDIRECTS } from "./src/lib/nav";

// All StewardAI settings live in one `.env` at the repository root (see /.env.example),
// shared with the Python backend. Load it before Next inlines NEXT_PUBLIC_* values.
// Only unset variables are filled, so the process environment (CI, Vercel) and any
// apps/web/.env* file Next has already loaded take precedence. Empty `KEY=` lines are
// skipped so they count as unset, as they do in the backend.
function loadRepoRootEnv(): void {
  let contents: string;
  try {
    contents = readFileSync(path.resolve(process.cwd(), "../../.env"), "utf8");
  } catch {
    return; // No file (e.g. on Vercel): rely on the environment.
  }
  for (const [key, value] of Object.entries(parseEnv(contents))) {
    if (value !== "" && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

loadRepoRootEnv();

const nextConfig: NextConfig = {
  // Moved routes (lib/nav.ts REDIRECTS). Temporary until the IA migration is complete.
  async redirects() {
    return REDIRECTS.map((r) => ({ ...r, permanent: false }));
  },
  // Private app: keep every response (including static assets) out of search indexes.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow, noarchive, nosnippet" }],
      },
    ];
  },
};

export default nextConfig;
