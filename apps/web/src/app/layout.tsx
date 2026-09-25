import type { Metadata } from "next";

import { ThemeProvider } from "@/components/theme-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "StewardAI", template: "%s · StewardAI" },
  description: "Self-hosted AI portfolio copilot",
  robots: { index: false, follow: false },
  icons: { icon: { url: "/icon.svg", type: "image/svg+xml" } },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: next-themes sets the theme class before hydration.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-surface text-text">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
