/**
 * The /screen camera as the vision sensor (ADR 0010). Frames live only in this
 * tab's memory: each one is drawn to an offscreen canvas, run through
 * YOLO26-pose on-device (onnxruntime-web) and dropped. Only counts, anonymous
 * tracklets and distance bands leave, as `audience.snapshot`.
 *
 * One inference in flight; the camera's newest frame is the only one read
 * (stale frames are skipped, never queued).
 */
import type { AudienceSnapshot, DevPerception } from "@/contract";
import type * as Ort from "onnxruntime-web";
import { decodePeople, distanceBand, isLooking, letterbox, MODEL_SIZE } from "./pose";
import { Tracker } from "./tracker";

const MODEL_URL = "/models/yolo26n-pose.onnx";
/** Target cadence; the loop never runs faster than inference allows. */
const FRAME_INTERVAL_MS = 200;
/** The backend treats a vision agent silent for longer as gone (ws.py AGENT_SILENCE). */
const HEARTBEAT_MS = 1_000;

export type SnapshotFacts = Omit<AudienceSnapshot, "v" | "type" | "screen_id" | "seq">;
/** The console camera panel's boxes (no image): what the edge agent sends as `dev.perception`. */
export type PerceptionFacts = Omit<DevPerception, "v" | "type" | "screen_id" | "seq">;

const unit = (value: number) => Math.min(1, Math.max(0, value));

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

    constructor(
        private readonly onSnapshot: (facts: SnapshotFacts) => void,
        private readonly onPerception: (facts: PerceptionFacts) => void = () => {},
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
        ort.env.wasm.numThreads = 1; // static hosting sends no COOP/COEP: no threads
        const providers = "gpu" in navigator ? ["webgpu", "wasm"] : ["wasm"];
        this.session = await ort.InferenceSession.create(MODEL_URL, {
            executionProviders: providers,
            graphOptimizationLevel: "all",
        });
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
        const input = this.preprocess(video, width, height);
        const t0 = performance.now();
        const tensor = new ort.Tensor("float32", input, [1, 3, MODEL_SIZE, MODEL_SIZE]);
        const outputs = await session.run({ images: tensor });
        const inferenceMs = performance.now() - t0;
        const first = Object.values(outputs)[0];
        if (first === undefined) return;
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
        // A heartbeat when the room is unchanged and empty; every frame otherwise.
        if (people.length === 0 && now - this.lastSentMs < HEARTBEAT_MS) return;
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
            // No age model in the browser: unsettled evidence only blocks restricted
            // categories (invariant 2), it never counts as a detected child.
            minor_likely: { value: false, confidence: 0, uncertain: people.length > 0 },
            perception: {
                detector: this.detector,
                inference_ms: roundedMs,
                round_trip_ms: null,
                primary_attempt_ms: null,
            },
        });
    }

    /** Letterbox the frame into the model's square input, CHW float 0..1. */
    private preprocess(video: HTMLVideoElement, width: number, height: number): Float32Array {
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
