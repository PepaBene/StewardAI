import type { MetadataRoute } from "next";

// Private single-user app: ask every crawler to stay out. The noindex meta tag and
// X-Robots-Tag header (next.config.ts) cover crawlers that ignore robots.txt.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", disallow: "/" },
  };
}
