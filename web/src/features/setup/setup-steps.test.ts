import { describe, expect, it } from "vitest";
import { maxRate, nextStep, parseStep, parseTopUp, previousStep, strategyFor } from "./setup-steps";

describe("setup steps", () => {
    it("walks forward and back within bounds", () => {
        expect(nextStep("brand")).toBe("audience");
        expect(nextStep("bid")).toBe("live");
        expect(nextStep("live")).toBe("live");
        expect(previousStep("audience")).toBe("brand");
        expect(previousStep("brand")).toBe("brand");
    });

    it("reads an unknown step from the URL as the first one", () => {
        expect(parseStep("budget")).toBe("budget");
        expect(parseStep("nope")).toBe("brand");
        expect(parseStep(null)).toBe("brand");
    });
});

describe("bid strategies", () => {
    it("names the live multiplier, or null for a hand-tuned one", () => {
        expect(strategyFor("1")?.key).toBe("standard");
        expect(strategyFor("1.0")?.key).toBe("standard");
        expect(strategyFor("1.5")?.key).toBe("competitive");
        expect(strategyFor("1.7")).toBeNull();
    });

    it("scales a rate exactly, without float error", () => {
        expect(maxRate("6.5", "1.5")).toBe("9.75");
        expect(maxRate("8", "0.8")).toBe("6.4");
        expect(maxRate("0.1", "0.2")).toBe("0.02");
        expect(maxRate("4.3333", "1")).toBe("4.33");
        expect(maxRate("5", "2.5")).toBe("12.5");
    });
});

describe("top-up amounts", () => {
    it("accepts whole pesos in es-CL format within the cap", () => {
        expect(parseTopUp("25.000")).toBe(25_000);
        expect(parseTopUp("$ 25.000")).toBe(25_000);
        expect(parseTopUp("10000000")).toBe(10_000_000);
    });

    it("rejects empty, zero, decimals and amounts over the cap", () => {
        for (const raw of ["", "0", "1,5", "-3", "abc", "10000001"]) {
            expect(parseTopUp(raw)).toBeNull();
        }
    });
});
