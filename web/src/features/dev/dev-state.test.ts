import { DevOutboundSchema, type DevJob, type DevOutbound } from "@/contract";
import { describe, expect, it } from "vitest";
import {
    buildTimeline,
    buildTrace,
    devReducer,
    initialDevState,
    jobSpans,
    nextOnAir,
    providerLoad,
    WINDOWS,
    type DevState,
    type OnAir,
} from "./dev-state";

const T0 = Date.parse("2026-11-21T15:00:00.000Z");
const iso = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

const trace = {
    perception: null,
    candidate_at: null,
    detected_at: null,
    published_at: null,
    measured_at: iso(0),
    received_at: iso(40),
    moment_changed_at: iso(45),
    cleared_at: iso(60),
    ready_at: iso(80),
    displayed_at: null,
    upgraded_at: null,
    swap_not_before: null,
};

const spec = {
    layout_id: "duo",
    slots: [
        {
            brand_id: "b-1",
            brand_name: "Voltio",
            logo_url: "/kits/voltio.svg",
            copy_text: "Energía",
            accent_color: null,
            hero_image_url: null,
        },
    ],
    duration_ms: 8000,
    video_url: null,
    voiceover_url: null,
    qr_url: null,
    stages: [],
    offer: null,
    angle: "energy",
    copy_source: "stock",
    redemption_code: null,
    redeem_url: null,
};

const parse = (raw: Record<string, unknown>): DevOutbound =>
    DevOutboundSchema.parse({ v: 1, ...raw });

const ready = (creativeId: string, extra: Record<string, unknown> = {}) =>
    parse({
        type: "creative.ready",
        screen_id: "demo-01",
        creative_id: creativeId,
        auction_id: "a-1",
        spec,
        trace,
        ...extra,
    }) as OnAir;

const upgraded = (creativeId: string) =>
    parse({
        type: "creative.upgraded",
        screen_id: "demo-01",
        creative_id: creativeId,
        auction_id: "a-1",
        upgrade_kind: "copy",
        spec: { ...spec, copy_source: "ai" },
        trace: { ...trace, upgraded_at: iso(900) },
    }) as OnAir;

const job = (state: DevJob["state"], offsetMs: number, extra: Partial<DevJob> = {}): DevJob =>
    parse({
        type: "dev.job",
        screen_id: "demo-01",
        provider: "claude",
        key: "copy:demo-01:sig",
        kind: "copy",
        state,
        at: iso(offsetMs),
        elapsed_ms: null,
        error_type: null,
        load: [{ provider: "claude", in_flight: state === "running" ? 1 : 0 }],
        ...extra,
    }) as DevJob;

const feed = (messages: DevOutbound[], from: DevState = initialDevState) =>
    messages.reduce(
        (state, message, i) =>
            devReducer(state, { type: "message", message, receivedAtMs: T0 + i }),
        from,
    );

describe("dev reducer", () => {
    it("feeds console messages to the console reducer and keeps a per-screen mirror", () => {
        const s = feed([ready("cr-1")]);
        expect(s.console.screens["demo-01"]?.lastCreativeId).toBe("cr-1");
        expect(s.screens["demo-01"]?.onAir?.creative_id).toBe("cr-1");
        expect(s.screens["demo-01"]?.traces.map((t) => t.creativeId)).toEqual(["cr-1"]);
    });

    it("applies an upgrade only to the creative on air", () => {
        const onAir = ready("cr-2");
        expect(nextOnAir(onAir, upgraded("cr-1"))).toBe(onAir); // stale upgrade
        expect(nextOnAir(onAir, upgraded("cr-2"))?.spec.copy_source).toBe("ai");
        expect(nextOnAir(null, upgraded("cr-1"))?.creative_id).toBe("cr-1");
    });

    it("keeps a known displayed_at when a later trace for the same creative lacks it", () => {
        const latency = parse({
            type: "console.latency",
            screen_id: "demo-01",
            creative_id: "cr-1",
            trace: { ...trace, displayed_at: iso(1200) },
        });
        const s = feed([ready("cr-1"), latency, upgraded("cr-1")]);
        const [entry] = s.screens["demo-01"]?.traces ?? [];
        expect(entry?.trace.displayed_at).toBe(iso(1200));
        expect(entry?.trace.upgraded_at).toBe(iso(900));
    });

    it("bounds ring buffers and keeps them newest first", () => {
        const many = Array.from({ length: WINDOWS.jobs + 5 }, (_, i) => job("done", i));
        const s = feed(many);
        expect(s.jobs).toHaveLength(WINDOWS.jobs);
        expect(s.jobs[0]?.at).toBe(iso(WINDOWS.jobs + 4));
        expect(s.log).toHaveLength(WINDOWS.log < many.length ? WINDOWS.log : many.length);
    });

    it("never puts keyframe bytes in the log", () => {
        const keyframe = parse({
            type: "dev.keyframe",
            screen_id: "demo-01",
            measured_at: iso(0),
            jpeg_base64: "QUJDRA==",
        });
        const s = feed([keyframe]);
        expect(s.screens["demo-01"]?.keyframe?.frame.jpeg_base64).toBe("QUJDRA==");
        expect(JSON.stringify(s.log)).not.toContain("QUJDRA==");
    });

    it("pausing freezes the log but not the panels", () => {
        const paused = devReducer(initialDevState, { type: "pause", paused: true });
        const s = feed([ready("cr-1")], paused);
        expect(s.log).toHaveLength(0);
        expect(s.screens["demo-01"]?.onAir?.creative_id).toBe("cr-1");
    });

    it("remembers that the backend disabled the dev socket", () => {
        expect(devReducer(initialDevState, { type: "disabled" }).disabled).toBe(true);
    });
});

describe("job spans", () => {
    it("pairs submitted, running and done into one span", () => {
        const jobs = [job("done", 900), job("running", 100), job("submitted", 0)]; // newest first
        const [span] = jobSpans(jobs);
        expect(span).toMatchObject({ queuedMs: T0, startMs: T0 + 100, endMs: T0 + 900 });
        expect(span?.state).toBe("done");
        expect(providerLoad(jobs)).toEqual([{ provider: "claude", in_flight: 0 }]);
    });

    it("rebuilds a span from elapsed_ms when its start was missed, and skips dedupes", () => {
        const spans = jobSpans([job("failed", 500, { elapsed_ms: 300 }), job("deduped", 10)]);
        expect(spans).toHaveLength(1);
        expect(spans[0]).toMatchObject({ startMs: T0 + 200, endMs: T0 + 500, state: "failed" });
    });

    it("leaves a running job open", () => {
        expect(jobSpans([job("running", 100)])[0]).toMatchObject({ endMs: null, state: "running" });
    });
});

describe("timeline", () => {
    it("puts hot trace steps and warm spans on one axis", () => {
        const spans = jobSpans([job("done", 900), job("running", 100), job("submitted", 0)]);
        const timeline = buildTimeline(
            [{ creativeId: "cr-000001", trace: { ...trace, displayed_at: iso(400) } }],
            spans,
            { windowMs: 20_000, nowMs: T0 + 1000 },
        );
        expect(timeline?.endMs).toBe(T0 + 900);
        expect(timeline?.rows.map((r) => r.lane)).toEqual(["hot", "warm"]);
        expect(timeline?.rows[0]?.segments.map((s) => s.label)).toEqual([
            "red",
            "decisión",
            "creatividad",
            "pantalla",
        ]);
        expect(timeline?.rows[1]?.segments.map((s) => s.label)).toEqual(["en cola", "done"]);
    });

    it("drops rows that ended before the window and returns null when empty", () => {
        const old = jobSpans([job("done", 100), job("running", 0)]);
        const fresh = jobSpans([job("done", 60_000, { key: "voice:demo-01:k", kind: "voice" })]);
        const timeline = buildTimeline([], [...fresh, ...old], {
            windowMs: 20_000,
            nowMs: T0 + 60_000,
        });
        expect(timeline?.rows).toHaveLength(1);
        expect(buildTimeline([], [], { windowMs: 1000, nowMs: T0 })).toBeNull();
    });
});

describe("build trace", () => {
    it("collects a creative's steps and its brief's AI attempts, oldest first", () => {
        const brief = {
            brands: ["Voltio", "Café Patio"],
            layout: "duo",
            party: 2,
            people: 2,
            angle: "energy",
            angle_reason: "segment",
            offer_label: null,
            offer_price: null,
            held_brand: null,
            daypart: null,
            minor_likely: false,
            signature: "sig-1",
        };
        const step = (s: string, creativeId: string | null, offset: number, sig = "sig-1") =>
            parse({
                type: "dev.creative",
                screen_id: "demo-01",
                at: iso(offset),
                step: s,
                creative_id: creativeId,
                auction_id: null,
                moment_id: null,
                brief: { ...brief, signature: sig },
                copy_draft: null,
                outcome: null,
                ai_score: null,
                stock_score: null,
                violations: [],
                latency_ms: null,
                compose_ms: null,
                voiceover_url: null,
                video_url: null,
                redemption_code: null,
                note: null,
            });
        const s = feed([
            step("composed", "cr-1", 0),
            step("copy_attempt", null, 500),
            step("copy_attempt", null, 600, "other"),
            step("composed", "cr-2", 700, "other"),
        ]);
        const steps = buildTrace(s.screens["demo-01"]?.creatives ?? [], "cr-1");
        expect(steps.map((c) => c.step)).toEqual(["composed", "copy_attempt"]);
    });
});
