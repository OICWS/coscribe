import { describe, expect, it } from "vitest";
import { fitZoom, stepZoom, transitionLabel, zoomLabel } from "./slides";

describe("slide zoom", () => {
  it("fits the slide by its tighter side", () => {
    expect(fitZoom(960, 1000)).toBeCloseTo(0.5);
    expect(fitZoom(4000, 540)).toBeCloseTo(0.5);
    expect(zoomLabel(fitZoom(653, 1000))).toBe("34%");
  });

  it("steps through the presets from wherever it is", () => {
    expect(stepZoom(0.34, 1)).toBe(0.5);
    expect(stepZoom(0.34, -1)).toBe(0.25);
    expect(stepZoom(0.5, 1)).toBe(0.75);
    expect(stepZoom(0.25, -1)).toBe(0.25);
    expect(stepZoom(2, 1)).toBe(2);
  });

  it("names transitions as the properties panel shows them", () => {
    expect(transitionLabel(null)).toBe("None");
    expect(transitionLabel("fade")).toBe("Fade");
    expect(transitionLabel("morph")).toBe("Magic move");
  });
});
