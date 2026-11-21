import { assetUrl } from "@/lib/config";

/**
 * Voice-over playback through Web Audio (docs/stack.md §6): MP3s are fetched
 * and decoded into cached AudioBuffers independently of the visual swap.
 * A ready buffer plays immediately; late audio plays while its ad is on air.
 *
 * - Best effort: a voice that fails to load never blocks the visual swap.
 * - Each (creative, voice) plays once: an upgrade or a re-render doesn't repeat it.
 * - Output is capped (gain) so a voice-over never blasts a quiet venue.
 */

const MAX_BUFFERS = 16;
const OUTPUT_GAIN = 0.85;

type AudioContextFactory = () => AudioContext;
type PendingVoice = {
    controller: AbortController;
    promise: Promise<void>;
    users: number;
};

export class VoicePlayer {
    private context: AudioContext | null = null;
    private gain: GainNode | null = null;
    private readonly buffers = new Map<string, AudioBuffer>();
    private readonly played = new Set<string>();
    private readonly pending = new Map<string, PendingVoice>();
    private current: AudioBufferSourceNode | null = null;

    constructor(
        private readonly createContext: AudioContextFactory = () => new AudioContext(),
        private readonly fetcher: typeof fetch = (input, init) => fetch(input, init),
    ) {}

    private ensureContext(): { context: AudioContext; gain: GainNode } {
        if (this.context === null || this.gain === null) {
            this.context = this.createContext();
            this.gain = this.context.createGain();
            this.gain.gain.value = OUTPUT_GAIN;
            this.gain.connect(this.context.destination);
        }
        return { context: this.context, gain: this.gain };
    }

    isReady(url: string): boolean {
        return this.buffers.has(url);
    }

    /** Fetch + decode; callers handle optional audio failures. */
    async prepare(url: string, signal: AbortSignal): Promise<void> {
        signal.throwIfAborted();
        if (this.buffers.has(url)) return;
        let request = this.pending.get(url);
        if (request === undefined || request.controller.signal.aborted) {
            const controller = new AbortController();
            const fresh: PendingVoice = { controller, users: 0, promise: Promise.resolve() };
            fresh.promise = this.load(url, controller.signal).finally(() => {
                if (this.pending.get(url) === fresh) this.pending.delete(url);
            });
            request = fresh;
            this.pending.set(url, request);
        }
        const shared = request;
        shared.users++;
        return new Promise<void>((resolve, reject) => {
            const abort = () => {
                reject(new DOMException("Voice preparation cancelled", "AbortError"));
            };
            signal.addEventListener("abort", abort, { once: true });
            shared.promise.then(
                () => {
                    signal.removeEventListener("abort", abort);
                    resolve();
                },
                (error: unknown) => {
                    signal.removeEventListener("abort", abort);
                    reject(error instanceof Error ? error : new Error("Voice preparation failed"));
                },
            );
        }).finally(() => {
            // The last consumer cancels the request. Defer one microtask so the
            // back → front effect handoff reuses an in-flight decode or fetch.
            shared.users--;
            queueMicrotask(() => {
                if (shared.users === 0) shared.controller.abort();
            });
        });
    }

    private async load(url: string, signal: AbortSignal): Promise<void> {
        const response = await this.fetcher(assetUrl(url), { signal });
        if (!response.ok) throw new Error(`voice-over HTTP ${response.status}`);
        const bytes = await response.arrayBuffer();
        const { context } = this.ensureContext();
        const buffer = await context.decodeAudioData(bytes);
        signal.throwIfAborted();
        if (this.buffers.size >= MAX_BUFFERS) {
            const oldest = this.buffers.keys().next().value;
            if (oldest !== undefined) this.buffers.delete(oldest);
        }
        this.buffers.set(url, buffer);
    }

    /** Play once per (creative, url). Returns false when there is nothing ready to play. */
    play(creativeId: string, url: string): boolean {
        const key = `${creativeId}|${url}`;
        const buffer = this.buffers.get(url);
        if (buffer === undefined || this.played.has(key)) return false;
        const { context, gain } = this.ensureContext();
        if (context.state === "suspended") void context.resume();
        this.stop();
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(gain);
        source.start();
        this.current = source;
        this.played.add(key);
        return true;
    }

    /** A new creative took the screen: its predecessor's line must not keep talking. */
    stop(): void {
        try {
            this.current?.stop();
        } catch {
            // already ended
        }
        this.current = null;
    }
}
