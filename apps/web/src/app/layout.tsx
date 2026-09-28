import type { Metadata } from "next";

import { ThemeProvider } from "@/components/theme-provider";
import { colorModeInitScript } from "@/lib/color-mode";

import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "@fontsource/barlow-condensed/700.css";
import "@fontsource/ibm-plex-serif/400.css";
import "@fontsource/ibm-plex-serif/500.css";
import "@fontsource/ibm-plex-serif/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "StewardAI", template: "%s · StewardAI" },
  description: "Self-hosted AI portfolio copilot",
  robots: { index: false, follow: false },
  icons: { icon: { url: "/icon.svg", type: "image/svg+xml" } },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: next-themes and the inlined script below set classes on
    // <html> before hydration (theme and colourblind-alternate mode), so the server-rendered
    // markup never matches the first client render.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: colorModeInitScript }} />
      </head>
      <body className="min-h-dvh bg-surface text-text">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
