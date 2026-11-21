import type { ConsoleAuction, ConsoleBook } from "@/contract";
import { describe, expect, it } from "vitest";
import { bidRows, budgetLeft, splitRows, stepMultiplier } from "./bids";

const money = (amount: string) => ({ amount, currency: "CLP" });

function line(id: string, name: string, extra: Partial<ConsoleBook["lines"][number]> = {}) {
    return {
        campaign_id: id,
        brand_id: `b-${id}`,
        brand_name: name,
        category: "x",
        accent_color: "#ff0000",
        multiplier: "1",
        multiplier_seq: 0,
        in_stock: true,
        on_air: false,
        budget: money("50000"),
        spent: money("0"),
        reserved: money("0"),
        remaining: money("25000"),
        autobid: false,
        autobid_rule: null,
        autobid_reason: null,
        ...extra,
    };
}

const book = {
    type: "console.book",
    v: 1,
    screen_id: "demo-01",
    lines: [line("v", "Voltio", { on_air: true }), line("r", "Rayo"), line("m", "Malta Sur")],
} as unknown as ConsoleBook;

const exact = (id: string, label: string) => ({
    segment_id: id,
    bid_segment_id: id,
    kind: "exact",
    weight: 1,
    segment_label: label,
    bid_segment_label: label,
});

const auction = {
    bids: [
        {
            campaign_id: "v",
            bid_per_as: money("8"),
            value_per_s: money("5.52"),
            matches: [exact("408", "Fitness and Exercise")],
        },
        {
            campaign_id: "r",
            bid_per_as: money("6.5"),
            value_per_s: money("4.49"),
            matches: [
                {
                    segment_id: "977",
                    bid_segment_id: "980",
                    kind: "related",
                    weight: 0.4,
                    segment_label: "Coffee",
                    bid_segment_label: "Pastelería",
                },
            ],
        },
    ],
    winners: [{ campaign_id: "v", brand_name: "Voltio", price_per_as: money("5.2") }],
    exclusions: [
        { campaign_id: "r", reason: "competitor", detail: "Rayo: same category as Voltio" },
    ],
} as unknown as ConsoleAuction;

describe("bid table", () => {
    it("shows every brand before anyone was seen, with no result yet", () => {
        const rows = bidRows(book, null);
        expect(rows.map((r) => r.brand)).toEqual(["Voltio", "Rayo", "Malta Sur"]);
        expect(rows.every((r) => r.result === null && r.bidPerAs === null)).toBe(true);
    });

    it("joins the last auction: winner, exclusion with its reason, no interest", () => {
        const [voltio, rayo, malta] = bidRows(book, auction);
        expect(voltio).toMatchObject({ result: { label: "Gana" }, onAir: true });
        expect(voltio!.bidPerAs?.amount).toBe("8");
        expect(rayo).toMatchObject({
            result: { label: "Competidor" },
            why: "Rayo: same category as Voltio",
        });
        expect(malta!.result?.label).toBe("Sin interés");
    });

    it("steps the multiplier by 0.5 within the backend's bounds", () => {
        expect(stepMultiplier("1", 1)).toBe("1.5");
        expect(stepMultiplier("1", -1)).toBe("0.5");
        expect(stepMultiplier("0.5", -1)).toBeNull();
        expect(stepMultiplier("5", 1)).toBeNull();
        expect(stepMultiplier("1.2", 1)).toBe("1.5");
    });

    it("draws the budget bar from what is left", () => {
        expect(budgetLeft(bidRows(book, null)[0]!)).toBe(0.5);
    });
});

describe("ordering", () => {
    it("puts the winner first and folds brands without interest", () => {
        const { shown, idle } = splitRows(bidRows(book, auction));
        expect(shown.map((r) => r.brand)).toEqual(["Voltio", "Rayo"]);
        expect(idle.map((r) => r.brand)).toEqual(["Malta Sur"]);
    });

    it("lists every brand before anyone was seen", () => {
        expect(splitRows(bidRows(book, null)).idle).toEqual([]);
    });
});

describe("auction columns", () => {
    it("explains each bid: match, value share, price paid and reason", () => {
        const [voltio, rayo] = bidRows(book, auction);
        expect(voltio).toMatchObject({
            match: "Fitness and Exercise",
            matchKind: "exacto · 100 %",
            valueShare: 1,
            reason: "mejor valor para este grupo",
        });
        expect(voltio!.pays?.amount).toBe("5.2");
        expect(rayo).toMatchObject({
            match: "Pastelería → Coffee",
            matchKind: "relacionado · 40 %",
            pays: null,
            reason: "same category as Voltio",
        });
        expect(rayo!.valueShare).toBeCloseTo(4.49 / 5.52);
    });
});
