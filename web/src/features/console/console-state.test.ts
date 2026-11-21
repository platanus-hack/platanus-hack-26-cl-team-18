import { ConsoleOutboundSchema, type DevPerception } from "@/contract";
import { describe, expect, it } from "vitest";
import { consoleReducer, initialConsoleState, REDEMPTION_WINDOW } from "./console-state";
import {
    coBrandLift,
    glassToGlassMs,
    holdMs,
    percentile,
    serverDecisionMs,
    traceSteps,
} from "./metrics";

const trace = {
    perception: null,
    candidate_at: null,
    detected_at: null,
    published_at: null,
    measured_at: "2026-11-21T15:00:00.000Z",
    received_at: "2026-11-21T15:00:00.004Z",
    moment_changed_at: "2026-11-21T15:00:00.010Z",
    cleared_at: "2026-11-21T15:00:00.030Z",
    ready_at: "2026-11-21T15:00:00.050Z",
    displayed_at: "2026-11-21T15:00:01.100Z",
    upgraded_at: null,
    swap_not_before: null,
};

describe("metrics", () => {
    it("nearest-rank percentiles", () => {
        const samples = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000];
        expect(percentile(samples, 50)).toBe(500);
        expect(percentile(samples, 95)).toBe(1000);
        expect(percentile([], 50)).toBeNull();
    });

    it("tracks server decision time independently of confirmed display", () => {
        expect(glassToGlassMs(trace)).toBe(1100);
        expect(glassToGlassMs({ ...trace, displayed_at: null })).toBeNull();
        expect(serverDecisionMs({ ...trace, displayed_at: null })).toBe(46);
        expect(glassToGlassMs({ ...trace, measured_at: null })).toBeNull();
    });

    it("excludes the minimum-ad-seconds hold from latency", () => {
        const held = { ...trace, swap_not_before: "2026-11-21T15:00:01.030Z" }; // 1 s hold
        expect(holdMs(held)).toBe(1000);
        expect(glassToGlassMs(held)).toBe(100);
        expect(holdMs({ ...trace, swap_not_before: "2026-11-21T15:00:00.000Z" })).toBe(0);
    });

    it("trace steps carry deltas in loop order", () => {
        const steps = traceSteps(trace);
        expect(steps.map((s) => s.step)).toEqual([
            "measured",
            "received",
            "moment_changed",
            "cleared",
            "ready",
            "displayed",
        ]);
        expect(steps[1]?.deltaMs).toBe(4);
    });

    it("co-brand lift", () => {
        expect(coBrandLift("1.50", "1.00")).toBeCloseTo(0.5);
        expect(coBrandLift("1.00", "0")).toBeNull();
    });
});

describe("console reducer", () => {
    it("validates through the generated schema and reduces per screen", () => {
        const latency = ConsoleOutboundSchema.parse({
            type: "console.latency",
            v: 1,
            screen_id: "mall-01",
            creative_id: "cr-1",
            trace,
        });
        const s = consoleReducer(initialConsoleState, { type: "message", message: latency });
        expect(s.screens["mall-01"]?.glassToGlassMs).toEqual([1100]);
        expect(s.screens["mall-01"]?.lastCreativeId).toBe("cr-1");
    });

    it("rejects frames that don't match the contract", () => {
        expect(ConsoleOutboundSchema.safeParse({ type: "console.latency", v: 2 }).success).toBe(
            false,
        );
        expect(ConsoleOutboundSchema.safeParse({ type: "nope" }).success).toBe(false);
    });

    it("tracks connection state without losing screen data", () => {
        let s = consoleReducer(initialConsoleState, {
            type: "message",
            message: {
                type: "console.degradation",
                v: 1,
                screen_id: "s1",
                flags: ["vlm_unavailable"],
            },
        });
        s = consoleReducer(s, {
            type: "connection",
            state: { status: "reconnecting", attempt: 1, retryInMs: 250 },
        });
        expect(s.connection.status).toBe("reconnecting");
        expect(s.screens["s1"]?.degradation).toEqual(["vlm_unavailable"]);
    });
});

describe("market and receipts", () => {
    const money = (amount: string) => ({ amount, currency: "CLP" });
    const receipt = (creative: string, total: string) =>
        ConsoleOutboundSchema.parse({
            type: "console.receipt",
            v: 1,
            screen_id: "demo-01",
            creative_id: creative,
            auction_id: "a1",
            people: 2,
            looking: 2,
            played_s: "4.000",
            attention_seconds: total === "0" ? "0.000" : "8.000",
            charges: [],
            total: money(total),
            filtered: [],
        });

    it("keeps receipts newest first, every play window separately", () => {
        let state = initialConsoleState;
        for (const message of [receipt("c1", "26"), receipt("c1", "0"), receipt("c2", "9")]) {
            state = consoleReducer(state, { type: "message", message });
        }
        const view = state.screens["demo-01"];
        expect(view?.receipts.map((r) => [r.creative_id, r.total.amount])).toEqual([
            ["c2", "9"],
            ["c1", "0"],
            ["c1", "26"],
        ]);
    });

    const redemption = (code: string, screen = "demo-01") =>
        ConsoleOutboundSchema.parse({
            type: "console.redemption",
            v: 1,
            screen_id: screen,
            auction_id: "a1",
            code,
            brands: ["Voltio", "Brasa"],
            offer_text: "Dúo $5.990",
            redeemed_at: "2026-11-21T12:00:00Z",
        });

    it("keeps the last redemptions newest first, per screen", () => {
        let state = initialConsoleState;
        for (let i = 0; i < REDEMPTION_WINDOW + 2; i++) {
            state = consoleReducer(state, {
                type: "message",
                message: redemption(`CODE${String(i).padStart(2, "0")}`),
            });
        }
        state = consoleReducer(state, { type: "message", message: redemption("OTHER1", "s2") });
        const codes = state.screens["demo-01"]?.redemptions.map((r) => r.code);
        expect(codes).toHaveLength(REDEMPTION_WINDOW);
        expect(codes?.[0]).toBe("CODE11");
        expect(codes).not.toContain("CODE01");
        expect(state.screens["s2"]?.redemptions.map((r) => r.brands)).toEqual([
            ["Voltio", "Brasa"],
        ]);
    });

    it("a redemption before any receipt leaves the receipts empty", () => {
        const state = consoleReducer(initialConsoleState, {
            type: "message",
            message: redemption("AB12CD"),
        });
        expect(state.screens["demo-01"]?.receipts).toEqual([]);
        expect(state.screens["demo-01"]?.redemptions[0]?.code).toBe("AB12CD");
    });

    it("stores the live book per screen", () => {
        const book = ConsoleOutboundSchema.parse({
            type: "console.book",
            v: 1,
            screen_id: "demo-01",
            lines: [
                {
                    campaign_id: "c",
                    brand_id: "b",
                    brand_name: "Voltio",
                    category: "sports_goods",
                    accent_color: "#c6f26b",
                    multiplier: "1.5",
                    multiplier_seq: 2,
                    in_stock: true,
                    on_air: true,
                    budget: money("50000"),
                    spent: money("26"),
                    reserved: money("0"),
                    remaining: money("49974"),
                    autobid: false,
                    autobid_rule: null,
                    autobid_reason: null,
                },
            ],
        });
        const state = consoleReducer(initialConsoleState, { type: "message", message: book });
        expect(state.screens["demo-01"]?.book?.lines[0]?.multiplier).toBe("1.5");
    });
});

it("includes sensing confirmation in the display metric and waterfall", () => {
    const sensed = { ...trace, candidate_at: "2026-11-21T14:59:59.300Z" };
    expect(glassToGlassMs(sensed)).toBe(1800);
    expect(traceSteps(sensed)[1]?.deltaMs).toBe(700);
});

describe("what airs and the timeline", () => {
    const spec = {
        layout_id: "solo",
        slots: [
            {
                brand_id: "voltio",
                brand_name: "Voltio",
                logo_url: "/v.svg",
                copy_text: "Recarga",
                accent_color: "#c6f26b",
                hero_image_url: null,
            },
        ],
        duration_ms: 12000,
        video_url: null,
        voiceover_url: null,
        qr_url: null,
        stages: [],
        offer: null,
        angle: null,
        copy_source: null,
        redemption_code: null,
        redeem_url: null,
    };
    const ready = (id: string, auction: string | null) =>
        ConsoleOutboundSchema.parse({
            type: "creative.ready",
            v: 1,
            screen_id: "demo-01",
            creative_id: id,
            auction_id: auction,
            spec,
            trace,
        });

    it("keeps the creative on air and narrates it with the receive time", () => {
        let state = consoleReducer(initialConsoleState, {
            type: "message",
            message: ready("c1", "a1"),
            at: 1000,
        });
        const view = state.screens["demo-01"]!;
        expect(view.onAir?.creative_id).toBe("c1");
        expect(view.events[0]).toEqual({ at: 1000, text: "Al aire: Voltio", tone: "success" });
        state = consoleReducer(state, { type: "message", message: ready("h", null), at: 2000 });
        expect(state.screens["demo-01"]!.events.map((e) => e.text)).toEqual([
            "Al aire: aviso de la casa",
            "Al aire: Voltio",
        ]);
    });

    it("an upgrade replaces only the ad it belongs to", () => {
        let state = consoleReducer(initialConsoleState, {
            type: "message",
            message: ready("c2", "a2"),
        });
        const stale = ConsoleOutboundSchema.parse({
            ...ready("c1", "a1"),
            type: "creative.upgraded",
            upgrade_kind: "copy",
        });
        state = consoleReducer(state, { type: "message", message: stale });
        expect(state.screens["demo-01"]!.onAir?.creative_id).toBe("c2");
        expect(state.screens["demo-01"]!.onAir?.type).toBe("creative.ready");
    });
});

describe("the next ad and the camera", () => {
    const spec = {
        layout_id: "duo",
        slots: [
            {
                brand_id: "voltio",
                brand_name: "Voltio",
                logo_url: "/v.svg",
                copy_text: "Recarga",
                accent_color: "#c6f26b",
                hero_image_url: null,
            },
        ],
        duration_ms: 12000,
        video_url: null,
        voiceover_url: null,
        qr_url: null,
        stages: [],
        offer: null,
        angle: null,
        copy_source: null,
        redemption_code: null,
        redeem_url: null,
    };

    it("keeps the waiting ad until an ad airs", () => {
        const next = ConsoleOutboundSchema.parse({
            type: "console.next",
            v: 1,
            screen_id: "demo-01",
            auction_id: "a2",
            spec,
            not_before: "2026-11-21T15:00:02Z",
        });
        let state = consoleReducer(initialConsoleState, { type: "message", message: next, at: 5 });
        expect(state.screens["demo-01"]!.next?.auction_id).toBe("a2");
        expect(state.screens["demo-01"]!.events[0]!.text).toContain("Siguiente");
        const aired = ConsoleOutboundSchema.parse({
            type: "creative.ready",
            v: 1,
            screen_id: "demo-01",
            creative_id: "c2",
            auction_id: "a2",
            spec,
            trace,
        });
        state = consoleReducer(state, { type: "message", message: aired });
        expect(state.screens["demo-01"]!.next).toBeNull();
    });

    it("stores the latest detections with their receive time", () => {
        const perception = {
            type: "dev.perception" as const,
            v: 1 as const,
            screen_id: "demo-01",
            seq: 1,
            measured_at: "2026-11-21T15:00:00Z",
            frame_w: 1280,
            frame_h: 720,
            detector: "local",
            inference_ms: 38,
            round_trip_ms: null,
            raw_people: 1,
            people: [],
            objects: [],
            markers: [],
            held: [],
            frames: 1,
            stale_frames: 0,
            preview_jpeg_base64: null,
        } satisfies DevPerception;
        const state = consoleReducer(initialConsoleState, {
            type: "perception",
            message: perception,
            at: 1234,
        });
        expect(state.screens["demo-01"]!.perception).toEqual({ message: perception, at: 1234 });
    });
});
