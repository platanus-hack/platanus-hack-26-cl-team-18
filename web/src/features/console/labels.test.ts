import { describe, expect, it } from "vitest";
import { count, signedPct } from "./labels";

describe("console labels", () => {
    it("signs a lift both ways, never '+-'", () => {
        expect(signedPct(0.07)).toBe("+7 %");
        expect(signedPct(-0.21)).toBe("−21 %");
        expect(signedPct(0.001)).toBe("0 %");
    });

    it("pluralizes counts", () => {
        expect(count(1, "persona", "personas")).toBe("1 persona");
        expect(count(3, "persona", "personas")).toBe("3 personas");
    });
});
