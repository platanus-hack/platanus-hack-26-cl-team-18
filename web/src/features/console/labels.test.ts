import type { BidMatch } from "@/contract";
import { describe, expect, it } from "vitest";
import { affinityRoutes, count, signedPct } from "./labels";

const match = (over: Partial<BidMatch>): BidMatch => ({
    segment_id: "977",
    bid_segment_id: "977",
    kind: "exact",
    weight: 1,
    segment_label: "Coffee",
    bid_segment_label: "Coffee",
    ...over,
});

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

    it("explains approximate matches and stays silent on exact ones", () => {
        expect(affinityRoutes([])).toBe("");
        expect(affinityRoutes([match({})])).toBe("");
        expect(
            affinityRoutes([
                match({}),
                match({
                    segment_id: "1045",
                    segment_label: "Pastry/Doughnuts",
                    kind: "related",
                    weight: 0.4,
                }),
            ]),
        ).toBe("vía Coffee → Pastry/Doughnuts · relacionado 40 %");
    });

    it("joins several routes, rounds weights and falls back to ids", () => {
        expect(
            affinityRoutes([
                match({
                    bid_segment_id: "607",
                    bid_segment_label: "",
                    segment_id: "625",
                    segment_label: "",
                    kind: "broader",
                    weight: 0.64,
                }),
                match({
                    bid_segment_label: "Cycling",
                    segment_label: "Sports",
                    kind: "narrower",
                    weight: 0.4,
                }),
            ]),
        ).toBe("vía 607 → 625 · más amplio 64 % · vía Cycling → Sports · más específico 40 %");
    });
});
