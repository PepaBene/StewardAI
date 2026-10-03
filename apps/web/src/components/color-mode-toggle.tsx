"use client";

import { ContrastIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useColorMode } from "@/lib/color-mode";

/**
 * Colourblind-alternate mode: adds a pattern fill to gain/loss backgrounds and a distinct
 * underline style to gain/loss text, for viewers who need more than the validated hue pair
 * (including full monochromacy). Independent of, and stacks with, light/dark.
 */
export function ColorModeToggle() {
  const [enabled, setEnabled] = useColorMode();
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Colourblind-friendly gain/loss patterns"
      // The icon's colour follows the `.cvd` class itself (via the `cvd:` variant, set
      // before paint by the blocking script — see color-mode.ts), so it never needs a
      // client-only branch. aria-pressed/title do need one; suppress the one-time
      // hydration warning that follows from a value only known once JS runs in the browser.
      aria-pressed={enabled}
      title={enabled ? "Colourblind-friendly patterns: on" : "Colourblind-friendly patterns: off"}
      suppressHydrationWarning
      onClick={() => setEnabled(!enabled)}
    >
      <ContrastIcon aria-hidden="true" className="cvd:text-accent-ink" />
    </Button>
  );
}
