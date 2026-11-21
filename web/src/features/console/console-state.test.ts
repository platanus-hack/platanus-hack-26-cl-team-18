import { ConsoleOutboundSchema } from "@/contract";
import { describe, expect, it } from "vitest";
import { consoleReducer, initialConsoleState } from "./console-state";
import { coBrandLift, glassToGlassMs, percentile, serverDecisionMs, traceSteps } from "./metrics";

const trace = {
    measured_at: "2026-11-21T15:00:00.000Z",
    received_at: "2026-11-21T15:00:00.004Z",
    moment_changed_at: "2026-11-21T15:00:00.010Z",
    cleared_at: "2026-11-21T15:00:00.030Z",
    ready_at: "2026-11-21T15:00:00.050Z",
    displayed_at: "2026-11-21T15:00:01.100Z",
    upgraded_at: null,
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
