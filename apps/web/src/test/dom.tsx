import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach } from "vitest";

/** Shared setup for DOM tests: unmount between tests, a user-event instance per render. */
afterEach(() => cleanup());

export function setup(ui: ReactElement) {
  const user = userEvent.setup();
  return { user, ...render(ui) };
}

export const active = () => document.activeElement as HTMLElement | null;
