import { describe, expect, it } from "vitest";
import { decimalCl, moneyCl } from "./format";

describe("es-CL display formatting", () => {
    it("groups thousands with dots and uses a decimal comma", () => {
        expect(moneyCl("49863")).toBe("$49.863");
        expect(moneyCl("2.275")).toBe("$2,275");
        expect(decimalCl("1234567.5")).toBe("1.234.567,5");
        expect(decimalCl("-1200")).toBe("-1.200");
    });

    it("pads or trims to a fixed number of decimals", () => {
        expect(decimalCl("1", 1)).toBe("1,0");
        expect(decimalCl("2.25", 1)).toBe("2,2");
    });
});
