/**
 * The /screen mic as the speech sensor (ADR 0005 + ADR 0010). Runs only while the
 * backend says `listening` (speech mode on for this screen, live, mic switch on);
 * the glass shows the mic chip meanwhile.
 *
 * Audio goes from the mic (browser echo cancellation on, so the ad's own voice is
 * not transcribed) to ElevenLabs Scribe Realtime with a single-use token the
 * backend mints; the API key never reaches the browser. Transcripts are
 * forwarded as ephemeral `audience.utterance` frames and never kept here.
 */
import type { AudienceUtterance } from "@/contract";
import { backendHttpUrl } from "@/lib/config";

const SCRIBE_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime";
const SAMPLE_RATE = 16_000;
/** ~128 ms of 16 kHz mono PCM16 per chunk (Scribe accepts 0.1–1 s). */
const CHUNK_SAMPLES = 2_048;
const RETRY_MS = 10_000;

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

/** Float32 at any rate → 16 kHz PCM16 (linear interpolation; speech-band is enough). */
export function toPcm16(input: Float32Array, fromRate: number): Int16Array {
    const ratio = fromRate / SAMPLE_RATE;
    const length = Math.floor(input.length / ratio);
    const out = new Int16Array(length);
    for (let i = 0; i < length; i++) {
        const pos = i * ratio;
        const j = Math.floor(pos);
        const a = input[j] ?? 0;
        const b = input[j + 1] ?? a;
        const sample = Math.max(-1, Math.min(1, a + (b - a) * (pos - j)));
        out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return out;
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
    private utterance = 0;
    /** Bumped by every start() and stop(): an older start that wakes up is stale. */
    private generation = 0;
    private language = "es";

    constructor(
        private readonly screenId: string,
        private readonly screenToken: string | null,
        private readonly onUtterance: (facts: UtteranceFacts) => void,
    ) {}

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
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                video: false,
            });
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
        const context = new AudioContext();
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
        const source = context.createMediaStreamSource(stream);
        const node = new AudioWorkletNode(context, "team18-capture");
        node.port.onmessage = (event: MessageEvent<Float32Array>) => {
            this.push(toPcm16(event.data, context.sampleRate));
        };
        source.connect(node); // not connected to the speakers: capture only
    }

    private push(pcm: Int16Array): void {
        const socket = this.socket;
        if (socket === null || socket.readyState !== WebSocket.OPEN) return; // drop, never queue
        this.pending.push(pcm);
        this.pendingSamples += pcm.length;
        if (this.pendingSamples < CHUNK_SAMPLES) return;
        const merged = new Int16Array(this.pendingSamples);
        let at = 0;
        for (const part of this.pending) {
            merged.set(part, at);
            at += part.length;
        }
        this.pending = [];
        this.pendingSamples = 0;
        socket.send(
            JSON.stringify({
                message_type: "input_audio_chunk",
                audio_base_64: base64(new Uint8Array(merged.buffer)),
                commit: false,
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
        const data = parsed as { message_type?: unknown; text?: unknown };
        const kind = typeof data.message_type === "string" ? data.message_type : undefined;
        const text = (typeof data.text === "string" ? data.text : "").trim().slice(0, 1000);
        if (kind === "partial_transcript") {
            if (text === "") return;
            this.emit(text, false);
        } else if (kind === "committed_transcript") {
            if (text !== "") this.emit(text, true);
            this.utterance++;
        } else if (kind !== undefined && kind !== "session_started" && kind.includes("error")) {
            console.warn(`[sensor] scribe: ${kind}`);
        }
    }

    private emit(text: string, isFinal: boolean): void {
        this.onUtterance({
            measured_at: new Date().toISOString(),
            text,
            language: this.language,
            keyterms_hit: [], // the backend matches keyterms itself (DEFAULT_KEYTERMS)
            is_final: isFinal,
            utterance_id: `w${this.utterance}`,
        });
    }

    private release(): void {
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
