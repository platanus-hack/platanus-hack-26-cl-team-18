/**
 * Demo-day failures of the in-browser camera sensor (ADR 0010), each one a way the
 * room could look empty (or occupied) forever on stage: camera denied or unplugged,
 * a UVC stall that freezes the picture, a model that 404s, a WebGPU device that
 * dies mid-run, a keyframe that fails to encode. The camera, the model and the
 * clock are fakes; the loop and its recovery are the real code.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ROW } from "./pose";
import { BrowserVision, type SnapshotFacts } from "./vision";

// ---- fake model (onnxruntime-web) ------------------------------------------------

interface FakeSession {
    run: ReturnType<typeof vi.fn>;
    release: ReturnType<typeof vi.fn>;
    provider: string;
}

type Output = "nobody" | "person" | "throw";

const model: {
    output: Output;
    createFails: Set<string>;
    created: { provider: string }[];
    sessions: FakeSession[];
} = vi.hoisted(() => ({
    /** Rows the next inference returns: one person, nobody, or a throw. */
    output: "nobody",
    createFails: new Set<string>(), // providers whose session creation rejects
    created: [],
    sessions: [],
}));

vi.mock("onnxruntime-web", () => {
    class Tensor {
        constructor(
            readonly type: string,
            readonly data: Float32Array,
            readonly dims: number[],
        ) {}
    }
    return {
        env: { wasm: { numThreads: 0 } },
        Tensor,
        InferenceSession: {
            create: vi.fn((_url: string, options: { executionProviders: string[] }) => {
                const provider = options.executionProviders[0] ?? "?";
                model.created.push({ provider });
                if (model.createFails.has(provider)) {
                    return Promise.reject(new Error(`${provider} unavailable`));
                }
                let warm = false;
                const session: FakeSession = {
                    provider,
                    release: vi.fn(() => Promise.resolve()),
                    run: vi.fn(() => {
                        if (!warm) {
                            warm = true; // the throwaway warm-up run always works
                            return Promise.resolve({ output0: { data: new Float32Array(ROW) } });
                        }
                        if (model.output === "throw")
                            return Promise.reject(new Error("device lost"));
                        return Promise.resolve({ output0: { data: modelRows(model.output) } });
                    }),
                };
                model.sessions.push(session);
                return Promise.resolve(session);
            }),
        },
    };
});

const keyframes = vi.hoisted(() => ({ fail: false, calls: 0 }));
vi.mock("./keyframe", () => ({
    allowlistKeyframe: vi.fn(() => {
        keyframes.calls++;
        if (keyframes.fail) return Promise.reject(new Error("encoder crashed"));
        return Promise.resolve({ jpeg: new ArrayBuffer(8), kept: [], heads: [] });
    }),
}));

/** One standing person, looking at the screen, filling the 416×416 letterboxed input. */
function modelRows(kind: "nobody" | "person"): Float32Array {
    const out = new Float32Array(ROW);
    if (kind === "nobody") return out;
    out.set([100, 100, 300, 400, 0.9, 0], 0);
    const kp = (i: number, x: number, y: number) => {
        out.set([x, y, 0.9], 6 + i * 3);
    };
    kp(0, 200, 150); // nose between the eyes → looking
    kp(1, 190, 140);
    kp(2, 210, 140);
    kp(5, 150, 200);
    kp(6, 250, 200);
    return out;
}

// ---- fake camera -----------------------------------------------------------------

interface FakeTrack {
    stop: ReturnType<typeof vi.fn>;
    /** Unplugged: the browser fires "ended". */
    ended: () => void;
    /** Ended with no event (the OS or another tab took the device): only readyState says so. */
    endSilently: () => void;
}

/**
 * How the browser reports new camera frames. Chromium has requestVideoFrameCallback,
 * and a stalled camera there keeps `currentTime` counting while no frame arrives
 * (measured), so only the callback reveals the stall. Without it, a stall shows as
 * `currentTime` standing still.
 */
type FramePath = "requestVideoFrameCallback" | "currentTime";

const camera = {
    /** "live" delivers new frames; "frozen" delivers none (a UVC stall). */
    picture: "live" as "live" | "frozen",
    frames: "requestVideoFrameCallback" as FramePath,
    width: 1280,
    height: 720,
    playFails: false,
    tracks: [] as FakeTrack[],
    getUserMedia: vi.fn(),
};

function fakeStream() {
    let onEnded: () => void = () => {};
    const track = {
        readyState: "live" as MediaStreamTrackState,
        stop: vi.fn(() => {
            track.readyState = "ended";
        }),
        addEventListener: (event: string, listener: () => void) => {
            if (event === "ended") onEnded = listener;
        },
    };
    camera.tracks.push({
        stop: track.stop,
        ended: () => {
            track.readyState = "ended";
            onEnded();
        },
        endSilently: () => {
            track.readyState = "ended";
        },
    });
    return { getTracks: () => [track], getVideoTracks: () => [track] };
}

function fakeVideo() {
    let frozenAt = 0;
    const video = {
        muted: false,
        playsInline: false,
        srcObject: null as unknown,
        play: () =>
            camera.playFails ? Promise.reject(new Error("NotAllowedError")) : Promise.resolve(),
        get videoWidth() {
            return camera.width;
        },
        get videoHeight() {
            return camera.height;
        },
        get currentTime() {
            if (camera.picture === "live" || camera.frames === "requestVideoFrameCallback") {
                frozenAt = Date.now() / 1000;
            }
            return frozenAt;
        },
    };
    if (camera.frames === "currentTime") return video;
    return Object.assign(video, {
        /** ~30 fps while live; a frozen camera never calls back until it recovers. */
        requestVideoFrameCallback(callback: (now: number) => void) {
            const fire = () => {
                if (camera.picture === "live") callback(performance.now());
                else setTimeout(fire, 33);
            };
            setTimeout(fire, 33);
            return 0;
        },
    });
}

let snapshots: SnapshotFacts[];
let detectors: string[];
let frameCloses: ReturnType<typeof vi.fn>;

beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] });
    model.output = "nobody";
    model.createFails.clear();
    model.created.length = 0;
    model.sessions.length = 0;
    keyframes.fail = false;
    keyframes.calls = 0;
    camera.picture = "live";
    camera.frames = "requestVideoFrameCallback";
    camera.width = 1280;
    camera.height = 720;
    camera.playFails = false;
    camera.tracks.length = 0;
    camera.getUserMedia = vi.fn(() => Promise.resolve(fakeStream()));
    snapshots = [];
    detectors = [];
    frameCloses = vi.fn();

    Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: (c: unknown) => camera.getUserMedia(c) as Promise<unknown> },
    });
    Object.defineProperty(navigator, "gpu", { configurable: true, value: {} });
    vi.stubGlobal("createImageBitmap", () => Promise.resolve({ close: frameCloses }));
    // The camera and canvas are fakes: jsdom has no media pipeline or 2D context.
    // eslint-disable-next-line @typescript-eslint/no-deprecated -- the string overload is the one vision.ts calls
    const create = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
        if (tag === "video") return fakeVideo();
        if (tag === "canvas") return { width: 0, height: 0, getContext: () => null };
        return create(tag);
        // eslint-disable-next-line @typescript-eslint/no-deprecated -- same overload
    }) as typeof document.createElement);
    vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "gpu");
});

function sensor(): BrowserVision {
    return new BrowserVision(
        (facts) => snapshots.push(facts),
        (facts) => detectors.push(facts.detector),
    );
}

const run = (ms: number) => vi.advanceTimersByTimeAsync(ms);

describe("BrowserVision on a demo day", () => {
    it("sees a person and reports them on every frame", async () => {
        model.output = "person";
        const vision = sensor();
        await vision.start();
        await run(1_000);
        expect(snapshots.length).toBeGreaterThanOrEqual(4);
        expect(snapshots.at(-1)?.people).toBe(1);
        expect(snapshots.at(-1)?.looking).toBe(1);
        // The tracklet id survives frame to frame, so dwell grows instead of resetting.
        const ids = new Set(snapshots.map((s) => s.tracklets[0]?.id));
        expect(ids.size).toBe(1);
        expect(snapshots.at(-1)?.tracklets[0]?.dwell_s).toBeGreaterThan(0.5);
        vision.stop();
    });

    it("an empty room sends a heartbeat about once a second, not every frame", async () => {
        const vision = sensor();
        await vision.start();
        await run(5_000);
        expect(snapshots.every((s) => s.people === 0)).toBe(true);
        expect(snapshots.length).toBeGreaterThanOrEqual(4);
        expect(snapshots.length).toBeLessThanOrEqual(6);
        vision.stop();
    });

    it("a denied or missing camera neither throws nor stays wedged: the next start retries", async () => {
        camera.getUserMedia = vi.fn(() =>
            Promise.reject(new DOMException("denied", "NotAllowedError")),
        );
        const vision = sensor();
        await expect(vision.start()).resolves.toBeUndefined();
        await run(1_000);
        expect(snapshots).toHaveLength(0);

        camera.getUserMedia = vi.fn(() => Promise.resolve(fakeStream()));
        await vision.start();
        await run(1_000);
        expect(camera.getUserMedia).toHaveBeenCalledTimes(1);
        expect(snapshots.length).toBeGreaterThan(0);
        vision.stop();
    });

    it("a model that fails to load never opens the camera, and loads on the next start", async () => {
        model.createFails.add("webgpu");
        model.createFails.add("wasm");
        const vision = sensor();
        await expect(vision.start()).resolves.toBeUndefined();
        expect(camera.getUserMedia).not.toHaveBeenCalled();

        model.createFails.clear();
        await vision.start();
        await run(500);
        expect(snapshots.length).toBeGreaterThan(0);
        vision.stop();
    });

    it("a page autoplay refusal releases the camera instead of holding it", async () => {
        camera.playFails = true;
        const vision = sensor();
        await vision.start();
        expect(camera.tracks[0]?.stop).toHaveBeenCalled();
        camera.playFails = false;
        await vision.start();
        await run(500);
        expect(snapshots.length).toBeGreaterThan(0);
        vision.stop();
    });

    it("no WebGPU adapter falls back to WASM and says so in the detector label", async () => {
        model.createFails.add("webgpu");
        const vision = sensor();
        await vision.start();
        await run(500);
        expect(model.created.map((c) => c.provider)).toEqual(["webgpu", "wasm"]);
        expect(detectors.at(-1)).toMatch(/^yolo26n-pose-web\/wasm-\d+t$/);
        vision.stop();
    });

    it.each<FramePath>(["requestVideoFrameCallback", "currentTime"])(
        "a frozen camera picture empties the room instead of showing the last person forever (%s)",
        async (path) => {
            camera.frames = path;
            model.output = "person";
            const vision = sensor();
            await vision.start();
            await run(1_000);
            expect(snapshots.at(-1)?.people).toBe(1);

            camera.picture = "frozen"; // UVC stall: same frame, forever
            await run(4_000);
            expect(camera.tracks[0]?.stop).toHaveBeenCalled();
            const count = snapshots.length;
            await run(2_000);
            expect(snapshots.length).toBe(count); // stopped: no more "someone is here"

            // The next live.status restarts it and the unstuck camera works again.
            camera.picture = "live";
            await vision.start();
            await run(1_000);
            expect(camera.getUserMedia).toHaveBeenCalledTimes(2);
            expect(snapshots.length).toBeGreaterThan(count);
            vision.stop();
        },
    );

    it.each<FramePath>(["requestVideoFrameCallback", "currentTime"])(
        "a still scene with a live camera is not mistaken for a frozen one (%s)",
        async (path) => {
            camera.frames = path;
            const vision = sensor();
            await vision.start();
            await run(10_000);
            expect(camera.tracks[0]?.stop).not.toHaveBeenCalled();
            vision.stop();
        },
    );

    it("a camera taken by another app (ended, no event) is let go instead of read forever", async () => {
        model.output = "person";
        const vision = sensor();
        await vision.start();
        await run(500);
        camera.tracks[0]?.endSilently();
        await run(500);
        const count = snapshots.length;
        await run(2_000);
        expect(snapshots.length).toBe(count);
        await vision.start();
        await run(500);
        expect(camera.getUserMedia).toHaveBeenCalledTimes(2);
        expect(snapshots.length).toBeGreaterThan(count);
        vision.stop();
    });

    it("an unplugged camera (track ended) releases the device so it can be reopened", async () => {
        const vision = sensor();
        await vision.start();
        await run(500);
        camera.tracks[0]?.ended();
        expect(camera.tracks[0]?.stop).toHaveBeenCalled();
        const count = snapshots.length;
        await run(2_000);
        expect(snapshots.length).toBe(count);

        await vision.start();
        await run(500);
        expect(camera.getUserMedia).toHaveBeenCalledTimes(2);
        vision.stop();
    });

    it("one bad frame is shrugged off; the loop keeps running", async () => {
        model.output = "person";
        const vision = sensor();
        await vision.start();
        await run(400);
        model.output = "throw";
        await run(250); // ≈ one frame fails
        model.output = "person";
        await run(400);
        model.output = "throw";
        await run(250);
        model.output = "person";
        await run(1_000);
        expect(camera.tracks[0]?.stop).not.toHaveBeenCalled();
        expect(model.sessions).toHaveLength(1);
        expect(snapshots.at(-1)?.people).toBe(1);
        vision.stop();
    });

    it("a dead WebGPU device rebuilds the session on WASM, never on the broken GPU", async () => {
        model.output = "person";
        const vision = sensor();
        await vision.start();
        await run(400);
        expect(detectors.at(-1)).toBe("yolo26n-pose-web/webgpu");

        model.output = "throw";
        await run(2_000);
        const gpu = model.sessions[0];
        expect(gpu?.release).toHaveBeenCalled();
        expect(camera.tracks[0]?.stop).toHaveBeenCalled();

        model.output = "person";
        await vision.start();
        await run(500);
        expect(model.created.map((c) => c.provider)).toEqual(["webgpu", "wasm"]);
        expect(detectors.at(-1)).toMatch(/wasm/);
        expect(snapshots.at(-1)?.people).toBe(1);
        vision.stop();
    });

    it("a keyframe that fails to encode neither stops the loop nor leaks the frame", async () => {
        model.output = "person";
        keyframes.fail = true;
        const vision = sensor();
        await vision.start();
        await run(4_000);
        expect(keyframes.calls).toBeGreaterThanOrEqual(2);
        expect(camera.tracks[0]?.stop).not.toHaveBeenCalled();
        expect(snapshots.at(-1)?.people).toBe(1);
        // Every frame grabbed was closed, keyframed or not (GPU memory on a kiosk).
        const grabbed = model.sessions[0]?.run.mock.calls.length ?? 0;
        expect(frameCloses.mock.calls.length).toBeGreaterThanOrEqual(grabbed - 2);
        vision.stop();
    });

    it("a camera that has not produced its first frame yet waits instead of failing", async () => {
        camera.width = 0;
        camera.height = 0;
        const vision = sensor();
        await vision.start();
        await run(2_000);
        expect(snapshots).toHaveLength(0);
        expect(camera.tracks[0]?.stop).not.toHaveBeenCalled();
        camera.width = 1280;
        camera.height = 720;
        await run(500);
        expect(snapshots.length).toBeGreaterThan(0);
        vision.stop();
    });

    it("standby during camera start-up closes the camera it was about to open", async () => {
        let grant: (stream: unknown) => void = () => {};
        camera.getUserMedia = vi.fn(
            () =>
                new Promise((resolve) => {
                    grant = resolve;
                }),
        );
        const vision = sensor();
        const starting = vision.start();
        await run(0);
        vision.stop(); // live.status flipped to standby mid-permission-prompt
        grant(fakeStream());
        await starting;
        await run(1_000);
        expect(camera.tracks[0]?.stop).toHaveBeenCalled();
        expect(snapshots).toHaveLength(0);
    });

    it("start twice (two live.status in a row) opens the camera once", async () => {
        const vision = sensor();
        await Promise.all([vision.start(), vision.start()]);
        expect(camera.getUserMedia).toHaveBeenCalledTimes(1);
        vision.stop();
    });
});
