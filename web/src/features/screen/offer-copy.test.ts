import type { CreativeOffer } from "@/contract";
import { describe, expect, it } from "vitest";
import {
    copyFit,
    priceDigits,
    splitHyphenated,
    splitOfferHeadline,
    withoutBrandPrefix,
} from "./offer-copy";

describe("copyFit", () => {
    it.each([
        ["Los dos, un café para cerrar el día.", "regular"],
        ["Cuatro medialunas y cuatro néctares para seguir el viaje juntos.", "long"],
        [
            "Medialunas recién horneadas y leche chocolatada fría para los cuatro antes de volver a la ruta.",
            "longest",
        ],
    ] as const)("sizes %j as %s", (copy, fit) => {
        expect(copyFit(copy)).toBe(fit);
    });
});

const OFFER: CreativeOffer = {
    label: "Trío post-entreno",
    text: "Voltio + Café Patio x3",
    price: { amount: "8490", currency: "CLP" },
    party_size: 3,
};

describe("splitOfferHeadline", () => {
    it.each([
        ["Voltio + Café Patio x3 para los 3 por $8.490.", "Voltio + Café Patio x3 para los 3"],
        [
            "Pack x4 post-entreno: Voltio + Café Patio, $8.490",
            "Pack x4 post-entreno: Voltio + Café Patio",
        ],
        ["Voltio + Café Patio x3 · $8490", "Voltio + Café Patio x3"],
    ])("drops the approved trailing price from %j", (copy, headline) => {
        expect(splitOfferHeadline(copy, OFFER)).toBe(headline);
    });

    it.each([
        "Por $8.490, Voltio + Café Patio para los 3.", // price mid-sentence
        "Voltio + Café Patio x3 por $7.990.", // not the approved price
        "$8.490", // nothing would remain
    ])("keeps %j whole", (copy) => {
        expect(splitOfferHeadline(copy, OFFER)).toBe(copy);
    });

    it("leaves copy without an offer untouched", () => {
        expect(splitOfferHeadline("Café Patio · Café de grano.", null)).toBe(
            "Café Patio · Café de grano.",
        );
    });
});

it("formats CLP with Chilean thousands", () => {
    expect(priceDigits(OFFER)).toBe("8.490");
});

it.each([
    ["team18 · Acércate. Hay una oferta para ti.", "Acércate. Hay una oferta para ti."],
    ["Acércate. Hay una oferta para ti.", "Acércate. Hay una oferta para ti."],
    ["Voltio · Energía para tu día.", "Voltio · Energía para tu día."], // another brand's prefix stays
    ["team18 · ", "team18 · "], // nothing would remain
])("drops only the slot's own brand prefix from %j", (copy, shown) => {
    expect(withoutBrandPrefix(copy, "team18")).toBe(shown);
});

describe("splitHyphenated", () => {
    it("isolates hyphenated brand names so they can be kept on one line", () => {
        expect(splitHyphenated("Pronto Copec + Coca-Cola x2 para los 2")).toEqual([
            "Pronto Copec + ",
            "Coca-Cola",
            " x2 para los 2",
        ]);
        expect(splitHyphenated("Score + Juan Valdez x2")).toEqual(["Score + Juan Valdez x2"]);
        expect(splitHyphenated("Café + medialuna - $2.990")).toEqual(["Café + medialuna - $2.990"]);
    });
});
