/**
 * The /screen mic as the speech sensor (ADR 0005 + ADR 0010). Runs only while the
 * backend says `listening` (speech mode on for this screen, live, mic switch on);
 * the glass shows the mic chip meanwhile.
 *
 * Audio goes from the mic (browser echo cancellation on, so the ad's own voice is
 * not transcribed) to ElevenLabs Scribe Realtime with a single-use token the
 * backend mints; the API key never reaches the browser. Transcripts are
 * forwarded as ephemeral `audience.utterance` frames and never kept here.
 *
 * Accuracy and latency (measured live from Santiago, scribe_v3_live): the server
 * VAD commits after VAD_SILENCE_S of silence instead of its 1.5 s default (0.3 s
 * split "Uf, qué hambre. ¿Vamos por…" into two finals at the same latency);
 * `include_timestamps` feeds SpeechTurn's babble filter; capture runs at 16 kHz
 * so Chromium's resampler low-passes before decimating.
 */
import type { AudienceUtterance } from "@/contract";
import { backendHttpUrl } from "@/lib/config";
import { SpeechTurn } from "./turn";
import { deviceProblem, type DeviceState } from "./vision";

const SCRIBE_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime";
const SAMPLE_RATE = 16_000;
/** ~128 ms of 16 kHz mono PCM16 per chunk (Scribe accepts 0.1–1 s). */
const CHUNK_SAMPLES = 2_048;
const RETRY_MS = 10_000;
const VAD_SILENCE_S = "0.5";

/** Posts each 128-frame mic block to the main thread (copied; the worklet reuses it). */
const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
    process(inputs) {
        const channel = inputs[0] && inputs[0][0];
        if (channel) this.port.postMessage(channel.slice(0));
        return true;
    }
}
registerProcessor("team18-capture", Capture);
`;

export type UtteranceFacts = Omit<AudienceUtterance, "v" | "type" | "screen_id" | "seq">;

interface ScribeSession {
    token: string;
    model_id: string;
    language_code: string;
    keyterms: string[];
}

/**
 * Float32 at any rate → 16 kHz PCM16, across consecutive mic blocks. Each output
 * sample averages the input it covers (a box low-pass, so 48 kHz hiss doesn't
 * alias into the speech band), and the fractional read position carries over:
 * a 128-frame block at 48 kHz is 42.67 output samples, and dropping the 0.67
 * every block would cut 1.6% of the audio as clicks. Only used when the browser
 * can't open a 16 kHz AudioContext itself.
 */
export class Resampler {
    private readonly ratio: number;
    private tail = new Float32Array(0);
    private pos = 0;

    constructor(fromRate: number) {
        this.ratio = fromRate / SAMPLE_RATE;
    }

    push(input: Float32Array): Int16Array {
        const buf = new Float32Array(this.tail.length + input.length);
        buf.set(this.tail);
        buf.set(input, this.tail.length);
        const count = Math.max(0, Math.floor((buf.length - this.pos) / this.ratio));
        const out = new Int16Array(count);
        for (let k = 0; k < count; k++) {
            const start = this.pos + k * this.ratio;
            const from = Math.floor(start);
            const to = Math.max(from + 1, Math.min(buf.length, Math.floor(start + this.ratio)));
            let sum = 0;
            for (let j = from; j < to; j++) sum += buf[j] ?? 0;
            const mean = sum / (to - from);
            const sample = Number.isFinite(mean) ? Math.max(-1, Math.min(1, mean)) : 0;
            out[k] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        }
        const next = this.pos + count * this.ratio;
        const keep = Math.min(Math.floor(next), buf.length);
        this.tail = buf.slice(keep);
        this.pos = next - keep;
        return out;
    }
}

/** One block, no carry-over (tests and one-shot conversions). */
export function toPcm16(input: Float32Array, fromRate: number): Int16Array {
    return new Resampler(fromRate).push(input);
}

/** A 16 kHz context, or the device rate where the browser refuses one (old Firefox). */
function captureContext(stream: MediaStream): {
    context: AudioContext;
    source: MediaStreamAudioSourceNode;
} {
    try {
        const context = new AudioContext({ sampleRate: SAMPLE_RATE });
        try {
            return { context, source: context.createMediaStreamSource(stream) };
        } catch (error) {
            void context.close().catch(() => {});
            throw error;
        }
    } catch {
        const context = new AudioContext();
        return { context, source: context.createMediaStreamSource(stream) };
    }
}

/** Anonymous and page-local: tells this page's utterances apart from a reload's. */
function sessionTag(): string {
    return Math.random().toString(36).slice(2, 10) || "w";
}

function base64(bytes: Uint8Array): string {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

function stopTracks(stream: MediaStream): void {
    for (const track of stream.getTracks()) track.stop();
}

export class BrowserSpeech {
    private running = false;
    /** After a failed start, wait before asking for another token (no hammering). */
    private retryAfterMs = 0;
    private stream: MediaStream | null = null;
    private context: AudioContext | null = null;
    private socket: WebSocket | null = null;
    private pending: Int16Array[] = [];
    private pendingSamples = 0;
    /** Settled-turn commit asked for by SpeechTurn: rides on the next chunk. */
    private commitNext = false;
    private readonly turn: SpeechTurn;
    /** Bumped by every start() and stop(): an older start that wakes up is stale. */
    private generation = 0;
    private language = "es";

    constructor(
        private readonly screenId: string,
        private readonly screenToken: string | null,
        private readonly onUtterance: (facts: UtteranceFacts) => void,
        private readonly onState: (state: DeviceState) => void = () => {},
    ) {
        this.turn = new SpeechTurn(sessionTag(), ({ text, isFinal, utteranceId }) => {
            this.emit(text, isFinal, utteranceId);
        });
    }

    async start(): Promise<void> {
        if (this.running || Date.now() < this.retryAfterMs) return;
        this.running = true;
        // A stop() + start() while this one awaits (token fetch, mic prompt) supersedes
        // it: from then on it may only let go of what it opened itself.
        const run = ++this.generation;
        let stream: MediaStream | null = null;
        try {
            const session = await this.fetchSession();
            if (!this.current(run)) return; // the token is single-use and unspent: no cost
            this.language = session.language_code;
            this.setState("asking");
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                    },
                    video: false,
                });
            } catch (error) {
                if (this.current(run)) this.setState(deviceProblem(error));
                throw error;
            }
            if (!this.current(run)) {
                stopTracks(stream);
                return;
            }
            this.stream = stream;
            // Unplugged or permission revoked: close Scribe (it bills silence); the
            // next snapshot starts again and the mic prompt or backoff decides.
            const own = stream;
            for (const track of own.getAudioTracks()) {
                track.addEventListener("ended", () => {
                    if (this.stream === own) this.recover("mic track ended");
                });
            }
            this.openScribe(session);
            await this.openMic(stream, run);
            if (this.current(run)) this.setState("on");
        } catch (error) {
            if (!this.current(run)) {
                if (stream !== null) stopTracks(stream);
                return;
            }
            console.warn(`[sensor] speech unavailable: ${String(error)}`);
            this.retryAfterMs = Date.now() + RETRY_MS;
            this.running = false;
            this.release();
        }
    }

    stop(): void {
        this.running = false;
        this.generation++;
        this.release();
        // A refused or missing mic stays on the glass until a start gets past it.
        if (this.state === "on" || this.state === "asking") this.setState("off");
    }

    private state: DeviceState = "off";

    private setState(state: DeviceState): void {
        if (state === this.state) return;
        this.state = state;
        this.onState(state);
    }

    /** The mic was just allowed again: the next start() skips the failure backoff. */
    retryNow(): void {
        this.retryAfterMs = 0;
    }

    /** Re-read after an await: is the start that began as `run` still the live one? */
    private current(run: number): boolean {
        return this.running && this.generation === run;
    }

    private recover(reason: string): void {
        if (!this.running) return;
        console.warn(`[sensor] restarting speech: ${reason}`);
        this.stop();
    }

    private async fetchSession(): Promise<ScribeSession> {
        const headers: Record<string, string> = {};
        if (this.screenToken !== null) headers["Authorization"] = `Bearer ${this.screenToken}`;
        const response = await fetch(
            `${backendHttpUrl}/api/speech/token/${encodeURIComponent(this.screenId)}`,
            { method: "POST", headers },
        );
        if (!response.ok) throw new Error(`speech token ${response.status}`);
        return (await response.json()) as ScribeSession;
    }

    private openScribe(session: ScribeSession): void {
        const params = new URLSearchParams({
            model_id: session.model_id,
            token: session.token,
            audio_format: `pcm_${SAMPLE_RATE}`,
            language_code: session.language_code,
            commit_strategy: "vad",
            vad_silence_threshold_secs: VAD_SILENCE_S,
            include_timestamps: "true",
        });
        for (const term of session.keyterms.slice(0, 50)) params.append("keyterms", term);
        const socket = new WebSocket(`${SCRIBE_URL}?${params.toString()}`);
        socket.onmessage = (event) => {
            this.onScribe(String(event.data));
        };
        socket.onclose = () => {
            // Single-use token: a dropped session reopens with a fresh one, unless
            // a stop() or another start() came in meanwhile (standby, empty room).
            if (this.running && this.socket === socket) {
                this.stop();
                const dropped = this.generation;
                setTimeout(() => {
                    if (this.generation === dropped) void this.start();
                }, 1_000);
            }
        };
        this.socket = socket;
    }

    private async openMic(stream: MediaStream, run: number): Promise<void> {
        const { context, source } = captureContext(stream);
        this.context = context;
        const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: "text/javascript" }));
        try {
            await context.audioWorklet.addModule(url);
        } finally {
            URL.revokeObjectURL(url);
        }
        if (!this.current(run)) {
            void context.close().catch(() => {}); // stop() ran meanwhile
            return;
        }
        const node = new AudioWorkletNode(context, "team18-capture");
        const resampler = new Resampler(context.sampleRate);
        node.port.onmessage = (event: MessageEvent<Float32Array>) => {
            this.push(resampler.push(event.data));
        };
        source.connect(node); // not connected to the speakers: capture only
    }

    private push(pcm: Int16Array): void {
        const socket = this.socket;
        if (socket === null || socket.readyState !== WebSocket.OPEN) return; // drop, never queue
        this.pending.push(pcm);
        this.pendingSamples += pcm.length;
        if (this.turn.tick(Date.now())) this.commitNext = true;
        if (this.pendingSamples < CHUNK_SAMPLES) return;
        const merged = new Int16Array(this.pendingSamples);
        let at = 0;
        for (const part of this.pending) {
            merged.set(part, at);
            at += part.length;
        }
        this.pending = [];
        this.pendingSamples = 0;
        const commit = this.commitNext;
        this.commitNext = false;
        socket.send(
            JSON.stringify({
                message_type: "input_audio_chunk",
                audio_base_64: base64(new Uint8Array(merged.buffer)),
                commit,
                sample_rate: SAMPLE_RATE,
            }),
        );
    }

    private onScribe(raw: string): void {
        let parsed: unknown;
        try {
            parsed = JSON.parse(raw);
        } catch {
            return;
        }
        if (typeof parsed !== "object" || parsed === null) return; // `null`, a bare number
        const data = parsed as { message_type?: unknown; text?: unknown; words?: unknown };
        const kind = typeof data.message_type === "string" ? data.message_type : undefined;
        const text = (typeof data.text === "string" ? data.text : "").trim().slice(0, 1000);
        const now = Date.now();
        if (kind === "partial_transcript") {
            if (text === "") return;
            this.turn.partial(text, now);
        } else if (kind === "committed_transcript") {
            this.turn.committed(text, now);
        } else if (kind === "committed_transcript_with_timestamps") {
            this.turn.committedWithWords(text, data.words, now);
        } else if (kind !== undefined && kind !== "session_started" && kind.includes("error")) {
            console.warn(`[sensor] scribe: ${kind}`);
        }
    }

    private emit(text: string, isFinal: boolean, utteranceId: string): void {
        this.onUtterance({
            measured_at: new Date().toISOString(),
            text,
            language: this.language,
            keyterms_hit: [], // the backend matches keyterms itself (DEFAULT_KEYTERMS)
            is_final: isFinal,
            utterance_id: utteranceId,
            partials_stabilized: false, // raw Scribe partials: the backend's PartialGate decides
        });
    }

    private release(): void {
        this.turn.reset(Date.now()); // settle a final in flight, retract a half-heard one
        this.commitNext = false;
        const socket = this.socket;
        this.socket = null;
        if (socket !== null && socket.readyState <= WebSocket.OPEN) socket.close();
        void this.context?.close().catch(() => {});
        this.context = null;
        if (this.stream !== null) stopTracks(this.stream);
        this.stream = null;
        this.pending = [];
        this.pendingSamples = 0;
    }
}
