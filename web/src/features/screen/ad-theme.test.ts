import { describe, expect, it } from "vitest";
import { spirographPath, themeForHour } from "./ad-theme";

describe("themeForHour", () => {
    it.each([
        [6, "dark"],
        [7, "light"],
        [13, "light"],
        [19, "light"],
        [20, "dark"],
        [23, "dark"],
    ] as const)("hour %i reads %s", (hour, theme) => {
        expect(themeForHour(hour)).toBe(theme);
    });
});

describe("spirographPath", () => {
    it("is deterministic per basket and differs between baskets", () => {
        expect(spirographPath("voltio+patio")).toBe(spirographPath("voltio+patio"));
        expect(spirographPath("voltio+patio")).not.toBe(spirographPath("rayo+patio"));
    });

    it("stays inside the unit box", () => {
        const coords = spirographPath("rayo+patio", 400)
            .split(/[ML]/)
            .filter(Boolean)
            .flatMap((pair) => pair.split(" ").map(Number));
        expect(coords.every((v) => Math.abs(v) <= 1.0001)).toBe(true);
    });
});
