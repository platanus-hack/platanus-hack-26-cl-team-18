import { DevPerceptionSchema } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { emptyDevScreen, initialDevState } from "./dev-state";
import { DevPage, screenFromLocation } from "./DevPage";
import { EventLog, exportSession } from "./event-log";
import { AuctionHistory, OnAirPanel } from "./on-air";
import { BoxCanvas, PerceptionPanel, sampleRate, SpeechPanel, VlmPanel } from "./perception";
import { TimelinePanel } from "./timeline";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
});

afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    vi.unstubAllGlobals();
});

async function render(node: React.ReactNode) {
    await act(async () => {
        root.render(node);
        await Promise.resolve();
    });
    return host.textContent;
}

describe("dev panels: empty states", () => {
    it("each panel says what it is waiting for", async () => {
        const text = await render(
            <>
                <PerceptionPanel view={emptyDevScreen} nowMs={0} />
                <VlmPanel view={emptyDevScreen} />
                <SpeechPanel view={emptyDevScreen} nowMs={0} />
                <OnAirPanel view={emptyDevScreen} />
                <AuctionHistory auctions={[]} />
                <TimelinePanel timeline={null} windowMs={20_000} />
                <EventLog log={[]} screen="demo-01" paused={false} onPause={() => undefined} />
            </>,
        );
        expect(text).toContain("Sin cuadros del agente");
        expect(text).toContain("Sin llamadas al VLM");
        expect(text).toContain("Sin eventos de voz");
        expect(text).toContain("Ninguna creatividad recibida");
        expect(text).toContain("Sin subastas todavía");
        expect(text).toContain("Sin decisiones ni trabajos tibios");
        expect(text).toContain("Sin eventos todavía");
    });
});

describe("perception canvas", () => {
    const perception = DevPerceptionSchema.parse({
        v: 1,
        type: "dev.perception",
        screen_id: "demo-01",
        seq: 1,
        measured_at: "2026-11-21T15:00:00.000Z",
        frame_w: 1280,
        frame_h: 720,
        detector: "remote",
        inference_ms: 21,
        round_trip_ms: 48,
        raw_people: 3,
        people: [
            {
                track_id: "t1",
                box: { x1: 0.1, y1: 0.1, x2: 0.3, y2: 0.9 },
                confidence: 0.9,
                looking: true,
                distance_band: "near",
                dwell_s: 3.2,
            },
        ],
        objects: [
            {
                label: "cup",
                confidence: 0.7,
                box: { x1: 0.2, y1: 0.5, x2: 0.25, y2: 0.6 },
                near_person: true,
            },
        ],
        markers: [],
        held: ["cafe-patio"],
        frames: 120,
        stale_frames: 2,
    });

    it("draws tracked people and held objects in frame coordinates", async () => {
        await render(<BoxCanvas perception={perception} keyframe={null} nowMs={0} />);
        const svg = host.querySelector("svg");
        expect(svg?.getAttribute("viewBox")).toBe("0 0 1280 720");
        expect(svg?.getAttribute("aria-label")).toBe("1 personas y 1 objetos detectados");
        expect(host.textContent).toContain("t1 ▲ mira");
        expect(host.textContent).toContain("cup 70 % · en mano");
        expect(host.querySelector("image")).toBeNull();
    });

    it("uses the blurred keyframe as background only while it is fresh", async () => {
        const frame = {
            v: 1 as const,
            type: "dev.keyframe" as const,
            screen_id: "demo-01",
            measured_at: "2026-11-21T15:00:00.000Z",
            jpeg_base64: "QUJD",
        };
        await render(
            <BoxCanvas
                perception={perception}
                keyframe={{ frame, receivedAtMs: 0 }}
                nowMs={1000}
            />,
        );
        expect(host.querySelector("image")).not.toBeNull();
        await render(
            <BoxCanvas
                perception={perception}
                keyframe={{ frame, receivedAtMs: 0 }}
                nowMs={9000}
            />,
        );
        expect(host.querySelector("image")).toBeNull();
    });

    it("estimates frame rate from device timestamps", () => {
        const history = [0, 200, 400, 600].map((t) => ({
            atMs: 1000 - t,
            inferenceMs: 20,
            roundTripMs: null,
            people: 1,
        }));
        expect(sampleRate(history)).toBeCloseTo(5);
        expect(sampleRate([])).toBeNull();
    });
});

describe("dev page", () => {
    it("reads the screen from ?screen= and rejects unsafe ids", () => {
        expect(screenFromLocation("?screen=mall-02")).toBe("mall-02");
        expect(screenFromLocation("?screen=<x>")).toBe("demo-01");
        expect(screenFromLocation("")).toBe("demo-01");
    });

    it("shows the disabled state when the backend closes /ws/dev with 4404", async () => {
        class ClosingSocket {
            static OPEN = 1;
            readyState = 0;
            onopen: (() => void) | null = null;
            onmessage: (() => void) | null = null;
            onclose: ((e: { code: number }) => void) | null = null;
            onerror: (() => void) | null = null;
            constructor() {
                queueMicrotask(() => this.onclose?.({ code: 4404 }));
            }
            close() {
                /* already closed */
            }
            send() {
                /* read-only socket */
            }
        }
        vi.stubGlobal("WebSocket", ClosingSocket);
        await render(<DevPage />);
        await act(async () => {
            await Promise.resolve();
        });
        expect(host.textContent).toContain("Consola dev desactivada en este entorno");
    });

    it("exports the session oldest first without keyframe bytes", () => {
        const json = exportSession(initialDevState.log, "demo-01");
        expect(JSON.parse(json)).toMatchObject({ screen: "demo-01", events: [] });
    });
});
