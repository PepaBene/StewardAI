// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { active, setup } from "@/test/dom";

import { ConfirmAction } from "./confirm-action";

describe("ConfirmAction focus", () => {
  it("moves focus to Confirm when armed, and back to the trigger on Cancel", async () => {
    const run = vi.fn(async () => "done");
    const { user } = setup(<ConfirmAction label="Do it" confirmTitle="Do it?" confirmBody="body" run={run} />);
    const trigger = screen.getByRole("button", { name: "Do it" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(active()).toBe(screen.getByRole("button", { name: "Confirm" }));
    expect(run).not.toHaveBeenCalled();
    await user.keyboard("{Tab}{Enter}"); // Cancel
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(active()).toBe(trigger);
    expect(run).not.toHaveBeenCalled();
  });

  it("runs only on the second activation and returns focus to the trigger", async () => {
    const run = vi.fn(async () => "queued");
    const { user } = setup(<ConfirmAction label="Do it" confirmTitle="Do it?" confirmBody="body" run={run} />);
    const trigger = screen.getByRole("button", { name: "Do it" });
    await user.click(trigger);
    await user.keyboard("{Enter}"); // focus is on Confirm
    expect(run).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(active()).toBe(trigger);
  });
});
