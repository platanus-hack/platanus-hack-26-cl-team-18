import type { ConsoleOutbound } from "@/contract";
import { describe, expect, it } from "vitest";
import { consoleReducer, initialConsoleState, type ConsoleState } from "./console-state";

/**
 * Laptop B's console loses Wi-Fi or is reloaded mid-demo. On (re)connect the
 * backend replays this run's receipts and redemptions (app/bootstrap/demo.py), so
 * "Cobrado" and the lists match the backend, and nothing is counted twice.
 */
function receipt(creative: string, amount: string): ConsoleOutbound {
    return {
        type: "console.receipt",
        v: 1,
        screen_id: "demo-01",
        creative_id: creative,
        auction_id: `a-${creative}`,
        people: 2,
        looking: 2,
        played_s: "6.0",
        attention_seconds: "4.0",
        charges: [],
        total: { amount, currency: "CLP" },
        filtered: [],
    };
}

function redemption(code: string): ConsoleOutbound {
    return {
        type: "console.redemption",
        v: 1,
        screen_id: "demo-01",
        auction_id: "a-1",
        code,
        brands: ["Voltio", "Café Patio"],
        offer_text: null,
        redeemed_at: "2026-11-21T15:00:00.000Z",
    };
}

function feed(state: ConsoleState, messages: ConsoleOutbound[]): ConsoleState {
    return messages.reduce(
        (s, message) => consoleReducer(s, { type: "message", message, at: 1 }),
        state,
    );
}

describe("console resync", () => {
    it("a replay after a reconnect neither double counts nor drops a receipt", () => {
        // Seen live: r1, r2. Lost while the link was dead: r3. Replay: r1, r2, r3.
        let state = feed(initialConsoleState, [receipt("c1", "30"), receipt("c2", "12")]);
        state = feed(state, [receipt("c1", "30"), receipt("c2", "12"), receipt("c3", "8")]);
        const view = state.screens["demo-01"]!;
        expect(view.billed).toBe(50);
        expect(view.receipts.map((r) => r.creative_id)).toEqual(["c3", "c2", "c1"]);
        const lines = view.events.filter((e) => e.text.startsWith("Recibo"));
        expect(lines).toHaveLength(3);
    });

    it("a reloaded console shows the session total, not zero", () => {
        const state = feed(initialConsoleState, [receipt("c1", "30"), receipt("c2", "12")]);
        expect(state.screens["demo-01"]!.billed).toBe(42);
    });

    it("a replayed redemption is listed once", () => {
        let state = feed(initialConsoleState, [redemption("AB12")]);
        state = feed(state, [redemption("AB12"), redemption("CD34")]);
        expect(state.screens["demo-01"]!.redemptions.map((r) => r.code)).toEqual(["CD34", "AB12"]);
    });

    it("two different receipts for one creative (a play split by a reconnect) both count", () => {
        const second = { ...receipt("c1", "5"), played_s: "2.0" } as ConsoleOutbound;
        const state = feed(initialConsoleState, [receipt("c1", "30"), second]);
        expect(state.screens["demo-01"]!.billed).toBe(35);
    });
});
