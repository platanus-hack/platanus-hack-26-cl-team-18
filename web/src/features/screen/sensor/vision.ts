/**
 * The /screen camera as the vision sensor (ADR 0010). Frames live only in this
 * tab's memory: each one is drawn to an offscreen canvas, run through
 * YOLO26-pose on-device (onnxruntime-web) and dropped. Counts, anonymous
 * tracklets and distance bands leave as `audience.snapshot`; every 1.5 s while
 * someone is present, an allow-list keyframe (black except people, heads black)
 * goes to the backend for the warm tagger, as the edge agent does (keyframe.ts).
 *
 * One inference in flight; the camera's newest frame is the only one read
 * (stale frames are skipped, never queued).
 */
import type { AudienceKeyframe, AudienceSnapshot, DevPerception } from "@/contract";
import type * as Ort from "onnxruntime-web";
import { allowlistKeyframe } from "./keyframe";
import { MinorVote } from "./minor";
import { decodePeople, distanceBand, isLooking, letterbox, MODEL_SIZE } from "./pose";
import { Tracker } from "./tracker";

const MODEL_URL = "/models/yolo26n-pose.onnx";
/** Target cadence; the loop never runs faster than inference allows. */
const FRAME_INTERVAL_MS = 200;
/** The backend treats a vision agent silent for longer as gone (ws.py AGENT_SILENCE). */
const HEARTBEAT_MS = 1_000;
/** The edge agent's `keyframe_interval`. */
const KEYFRAME_INTERVAL_MS = 1_500;

export type SnapshotFacts = Omit<AudienceSnapshot, "v" | "type" | "screen_id" | "seq">;
/** The console camera panel's boxes (no image): what the edge agent sends as `dev.perception`. */
export type KeyframeFacts = Pick<AudienceKeyframe, "measured_at" | "kept" | "heads">;
export type PerceptionFacts = Omit<DevPerception, "v" | "type" | "screen_id" | "seq">;

const unit = (value: number) => Math.min(1, Math.max(0, value));

/**
 * WASM threads need SharedArrayBuffer, which needs a cross-origin isolated page
 * (COOP/COEP on /screen: terraform/modules/render-static-site). Half the cores,
 * at most 4: the player's video decode and the speech worklet share the CPU.
 */
function wasmThreads(): number {
    if (typeof crossOriginIsolated === "undefined" || !crossOriginIsolated) return 1;
    const cores = navigator.hardwareConcurrency || 2;
    return Math.max(1, Math.min(4, Math.ceil(cores / 2)));
}

export class BrowserVision {
    private session: Ort.InferenceSession | null = null;
    private ort: typeof Ort | null = null;
    private stream: MediaStream | null = null;
    private video: HTMLVideoElement | null = null;
    private canvas: OffscreenCanvas | HTMLCanvasElement | null = null;
    private timer: ReturnType<typeof setTimeout> | null = null;
    private running = false;
    private readonly tracker = new Tracker();
    private detector = "yolo26n-pose-web";

    private frames = 0;
    private readonly minor = new MinorVote();
    private lastKeyframeMs = 0;

    constructor(
        private readonly onSnapshot: (facts: SnapshotFacts) => void,
        private readonly onPerception: (facts: PerceptionFacts) => void = () => {},
        private readonly onKeyframe: (facts: KeyframeFacts, jpeg: ArrayBuffer) => void = () => {},
    ) {}

    async start(): Promise<void> {
        if (this.running) return;
        this.running = true;
        try {
            await this.load();
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
                audio: false,
            });
            if (!this.isRunning()) {
                this.release();
                return;
            }
            const video = document.createElement("video");
            video.muted = true;
            video.playsInline = true;
            video.srcObject = this.stream;
            await video.play();
            this.video = video;
            this.tick();
        } catch (error) {
            console.warn(`[sensor] camera unavailable: ${String(error)}`);
            this.running = false;
            this.release();
        }
    }

    /** Re-read after an await: `stop()` may have run meanwhile. */
    private isRunning(): boolean {
        return this.running;
    }

    stop(): void {
        this.running = false;
        if (this.timer !== null) clearTimeout(this.timer);
        this.timer = null;
        this.release();
        this.tracker.reset();
        this.minor.reset();
        this.lastKeyframeMs = 0;
    }

    private release(): void {
        this.stream?.getTracks().forEach((t) => {
            t.stop();
        });
        this.stream = null;
        if (this.video !== null) this.video.srcObject = null;
        this.video = null;
    }

    private async load(): Promise<void> {
        if (this.session !== null) return;
        const ort = await import("onnxruntime-web");
        ort.env.wasm.numThreads = wasmThreads();
        const options = { graphOptimizationLevel: "all" } as const;
        let session: Ort.InferenceSession | null = null;
        let backend = "wasm";
        if ("gpu" in navigator) {
            try {
                session = await ort.InferenceSession.create(MODEL_URL, {
                    ...options,
                    executionProviders: ["webgpu"],
                });
                backend = "webgpu";
            } catch {
                session = null; // no usable adapter: the CPU path below
            }
        }
        session ??= await ort.InferenceSession.create(MODEL_URL, {
            ...options,
            executionProviders: ["wasm"],
        });
        // The console's detector label says which path runs (and how many threads).
        this.detector =
            backend === "wasm"
                ? `yolo26n-pose-web/wasm-${String(ort.env.wasm.numThreads)}t`
                : "yolo26n-pose-web/webgpu";
        // One throwaway run so the first person seen doesn't pay for kernel setup.
        const size = 3 * MODEL_SIZE * MODEL_SIZE;
        await session.run({
            images: new ort.Tensor("float32", new Float32Array(size), [1, 3, MODEL_SIZE, MODEL_SIZE]),
        });
        this.session = session;
        this.ort = ort;
    }

    private tick = (): void => {
        if (!this.running) return;
        const started = performance.now();
        void this.step().finally(() => {
            if (!this.running) return;
            const wait = Math.max(0, FRAME_INTERVAL_MS - (performance.now() - started));
            this.timer = setTimeout(this.tick, wait);
        });
    };

    private lastSentMs = 0;

    private async step(): Promise<void> {
        const video = this.video;
        const session = this.session;
        const ort = this.ort;
        if (video === null || session === null || ort === null) return;
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (width === 0 || height === 0) return;
        const measuredAt = new Date();
        // One still for both inference and the keyframe: redacting a later frame
        // could show a face that moved out of the head box detected on this one.
        const frame = await createImageBitmap(video);
        let keyframing = false;
        try {
            keyframing = await this.detect(frame, width, height, measuredAt, session, ort);
        } finally {
            if (!keyframing) frame.close();
        }
    }

    /** Returns true when it handed `frame` to the keyframe encoder (which closes it). */
    private async detect(
        frame: ImageBitmap,
        width: number,
        height: number,
        measuredAt: Date,
        session: Ort.InferenceSession,
        ort: typeof Ort,
    ): Promise<boolean> {
        const input = this.preprocess(frame, width, height);
        const t0 = performance.now();
        const tensor = new ort.Tensor("float32", input, [1, 3, MODEL_SIZE, MODEL_SIZE]);
        const outputs = await session.run({ images: tensor });
        const inferenceMs = performance.now() - t0;
        const first = Object.values(outputs)[0];
        if (first === undefined) return false;
        const people = decodePeople(first.data as Float32Array, letterbox(width, height));
        const now = Date.now();
        const detections = people.map((p) => ({ person: p, looking: isLooking(p) }));
        const tracks = this.tracker.update(
            detections.map((d) => ({ box: d.person.box, looking: d.looking })),
            now,
        );
        this.frames++;
        const roundedMs = Math.round(inferenceMs * 10) / 10;
        this.onPerception({
            measured_at: measuredAt.toISOString(),
            frame_w: width,
            frame_h: height,
            detector: this.detector,
            inference_ms: roundedMs,
            round_trip_ms: null,
            raw_people: people.length,
            people: detections.slice(0, 64).map((d, i) => ({
                track_id: tracks[i]?.id ?? `b0-${i}`,
                box: {
                    x1: unit(d.person.box.x1 / width),
                    y1: unit(d.person.box.y1 / height),
                    x2: unit(d.person.box.x2 / width),
                    y2: unit(d.person.box.y2 / height),
                },
                confidence: unit(d.person.score),
                looking: d.looking,
                distance_band: distanceBand(d.person, width, height),
                dwell_s: Math.max(0, (now - (tracks[i]?.firstSeenMs ?? now)) / 1000),
            })),
            objects: [],
            markers: [],
            held: [],
            frames: this.frames,
            stale_frames: 0,
            preview_jpeg_base64: null,
        });
        const minor = this.minor.update(people, height);
        let keyframing = false;
        if (people.length > 0 && now - this.lastKeyframeMs >= KEYFRAME_INTERVAL_MS) {
            this.lastKeyframeMs = now;
            keyframing = true;
            void allowlistKeyframe(frame, width, height, people)
                .then((keyframe) => {
                    if (keyframe === null || !this.isRunning()) return;
                    this.onKeyframe(
                        {
                            measured_at: measuredAt.toISOString(),
                            kept: keyframe.kept,
                            heads: keyframe.heads,
                        },
                        keyframe.jpeg,
                    );
                })
                .catch((error: unknown) => {
                    console.warn(`[sensor] keyframe skipped: ${String(error)}`);
                })
                .finally(() => {
                    frame.close();
                });
        }
        // A heartbeat when the room is unchanged and empty; every frame otherwise.
        if (people.length === 0 && now - this.lastSentMs < HEARTBEAT_MS) return keyframing;
        this.lastSentMs = now;
        const looking = detections.filter((d) => d.looking).length;
        const at = measuredAt.toISOString();
        this.onSnapshot({
            measured_at: at,
            candidate_at: null,
            detected_at: new Date().toISOString(),
            published_at: new Date().toISOString(),
            people: people.length,
            looking,
            tracklets: detections.slice(0, 64).map((d, i) => ({
                id: tracks[i]?.id ?? `b0-${i}`,
                dwell_s: Math.max(0, (now - (tracks[i]?.firstSeenMs ?? now)) / 1000),
                looking: d.looking,
                distance_band: distanceBand(d.person, width, height),
            })),
            objects: [],
            sound_scene: [],
            // Pose-geometry vote (minor.ts, edge parity): it only ever blocks.
            minor_likely: minor,
            perception: {
                detector: this.detector,
                inference_ms: roundedMs,
                round_trip_ms: null,
                primary_attempt_ms: null,
            },
        });
        return keyframing;
    }

    /** Letterbox the frame into the model's square input, CHW float 0..1. */
    private preprocess(video: CanvasImageSource, width: number, height: number): Float32Array {
        const canvas = (this.canvas ??=
            typeof OffscreenCanvas !== "undefined"
                ? new OffscreenCanvas(MODEL_SIZE, MODEL_SIZE)
                : Object.assign(document.createElement("canvas"), {
                      width: MODEL_SIZE,
                      height: MODEL_SIZE,
                  }));
        const ctx = canvas.getContext("2d", { willReadFrequently: true }) as
            CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
        const out = new Float32Array(3 * MODEL_SIZE * MODEL_SIZE);
        if (ctx === null) return out;
        const lb = letterbox(width, height);
        ctx.fillStyle = "rgb(114,114,114)";
        ctx.fillRect(0, 0, MODEL_SIZE, MODEL_SIZE);
        ctx.drawImage(video, lb.padX, lb.padY, width * lb.scale, height * lb.scale);
        const pixels = ctx.getImageData(0, 0, MODEL_SIZE, MODEL_SIZE).data;
        const plane = MODEL_SIZE * MODEL_SIZE;
        for (let i = 0; i < plane; i++) {
            out[i] = (pixels[i * 4] ?? 0) / 255;
            out[plane + i] = (pixels[i * 4 + 1] ?? 0) / 255;
            out[2 * plane + i] = (pixels[i * 4 + 2] ?? 0) / 255;
        }
        return out;
    }
}
