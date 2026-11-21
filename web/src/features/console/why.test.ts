import type { ConsoleAuction, ConsoleMoment, CreativeReady } from "@/contract";
import { describe, expect, it } from "vitest";
import { baskets, explain } from "./why";

const money = (amount: string) => ({ amount, currency: "CLP" });

const moment = {
    type: "console.moment",
    moment_id: "m1",
    people: 3,
    looking: 3,
    segments: [
        { segment_id: "977", label: "Coffee", score: 0.82, sources: ["sound"] },
        { segment_id: "408", label: "Fitness and Exercise", score: 0.92, sources: ["vision"] },
    ],
    minor_likely: { value: false, confidence: 0.9 },
    held_products: [{ category: "cup", brand_ref: "cafe-patio", score: 0.9 }],
} as unknown as ConsoleMoment;

const auction = {
    moment_id: "m1",
    coverage: 1,
    winners: [{ brand_name: "Voltio" }, { brand_name: "Café Patio" }],
    exclusions: [
        { campaign_id: "r", reason: "competitor", detail: "Rayo: misma categoría que Voltio" },
        { campaign_id: "m", reason: "outbid", detail: "" },
    ],
    single_brand_value: money("9.75"),
    alternatives: [
        { brands: ["Voltio", "Café Patio"], value: money("9.83") },
        { brands: ["Voltio"], value: money("9.75") },
        { brands: ["Rayo", "Café Patio"], value: money("8.8") },
    ],
} as unknown as ConsoleAuction;

const onAir = {
    spec: { offer: { label: "Trío", text: "x", price: money("8490"), party_size: 3 } },
} as unknown as CreativeReady;

describe("explain", () => {
    it("answers in one sentence, then four short steps", () => {
        const why = explain(moment, auction, onAir)!;
        expect(why.headline).toBe(
            "Voltio + Café Patio, porque juntas llegan a las 3 personas y valen más que cualquier marca sola.",
        );
        expect(why.steps.map((s) => s.text)).toEqual([
            "3 personas mirando con Café Patio en la mano",
            "Fitness and Exercise · Coffee",
            "Voltio + Café Patio",
            "Trío $8.490 · $ 0 si nadie mira",
        ]);
        expect(why.leftOut).toBe(
            "Quedó fuera Rayo: misma categoría que Voltio. Nadie parece menor, así que no se bloqueó ninguna categoría.",
        );
    });

    it("a single winner and the house ad read naturally", () => {
        const solo = { ...auction, winners: [{ brand_name: "Voltio" }] } as ConsoleAuction;
        expect(explain(moment, solo, null)!.headline).toBe(
            "Voltio, porque es la marca que más vale para este grupo.",
        );
        const none = { ...auction, winners: [] } as unknown as ConsoleAuction;
        expect(explain(moment, none, null)!.headline).toContain("Aviso de la casa");
    });

    it("says nothing until the auction belongs to the moment on screen", () => {
        expect(explain(null, auction, null)).toBeNull();
        expect(explain(moment, { ...auction, moment_id: "old" }, null)).toBeNull();
    });
});

describe("baskets", () => {
    it("lists the winner, the single-brand baseline, the rest and the pairs never allowed", () => {
        expect(baskets(auction).map((b) => [b.name, b.tone])).toEqual([
            ["Voltio + Café Patio", "win"],
            ["Voltio sola", "base"],
            ["Rayo + Café Patio", "other"],
            ["Voltio + Rayo", "blocked"],
        ]);
    });
});
