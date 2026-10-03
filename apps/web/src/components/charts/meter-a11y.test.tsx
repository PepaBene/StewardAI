// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(() => cleanup());

import { Facts } from "@/components/xray/facts";

import { HBar } from "./hbar";
import { Gauge, Ring } from "./ring";
import { SrTable } from "./shared";

/** ARIA: a `meter` must have aria-valuenow. With no data (null) the chart is a labelled image. */
describe("meters without a value", () => {
  it("HBar with no value has no meter role and says so in its name", () => {
    render(<HBar value={null} label="Weight" />);
    expect(screen.queryByRole("meter")).toBeNull();
    expect(screen.getByRole("img", { name: /Weight: No data/ })).toBeTruthy();
  });
  it("HBar with a value is a meter with aria-valuenow", () => {
    render(<HBar value={0.4} label="Weight" />);
    expect(screen.getByRole("meter", { name: "Weight" }).getAttribute("aria-valuenow")).toBe("0.4");
  });
  it("Ring and Gauge with no value are not meters", () => {
    render(
      <>
        <Ring value={null} ariaLabel="Price coverage" />
        <Gauge value={null} max={10} ariaLabel="Concentration" />
      </>,
    );
    expect(screen.queryAllByRole("meter")).toHaveLength(0);
    expect(screen.getByRole("img", { name: /Price coverage: no data/ })).toBeTruthy();
  });
});

describe("SrTable", () => {
  it("never emits an empty column header (a screen reader would read nothing)", () => {
    const { container } = render(<SrTable caption="c" columns={["", " "]} rows={[["a", "1"]]} />);
    for (const th of container.querySelectorAll("thead th")) expect((th.textContent ?? "").trim()).not.toBe("");
  });
});

describe("Facts", () => {
  it("keeps a note inside the definition list's allowed children (dd), not a stray p", () => {
    const { container } = render(<Facts items={[{ label: "Value", value: "1", note: "of 3" }]} />);
    expect(container.querySelector("dl p")).toBeNull();
    expect(container.querySelectorAll("dl > div > dd")).toHaveLength(2);
  });
});
