/**
 * How BrowserSpeech asks Scribe for accuracy and speed: the session parameters,
 * clean 16 kHz capture, and the settled-turn commit riding on the next chunk.
 * Browser APIs are fakes; BrowserSpeech is the real code.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BrowserSpeech, Resampler, type UtteranceFacts } from "./speech";

class FakeScribe {
    static readonly OPEN = 1;
    static all: FakeScribe[] = [];
    readyState = 0;
    sent: { commit: boolean; audio_base_64: string }[] = [];
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    constructor(readonly url: string) {
        FakeScribe.all.push(this);
    }
    send(raw: string) {
        this.sent.push(JSON.parse(raw) as FakeScribe["sent"][number]);
    }
    close() {
        this.readyState = 3;
    }
    say(message: unknown) {
        this.onmessage?.({ data: JSON.stringify(message) });
    }
}

let worklet: ((event: { data: Float32Array }) => void) | null;
let contextOptions: unknown[];
let refuse16k: boolean;
let heard: UtteranceFacts[];

beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    FakeScribe.all = [];
    worklet = null;
    contextOptions = [];
    refuse16k = false;
    heard = [];
    vi.stubGlobal(
        "fetch",
        vi.fn(() =>
            Promise.resolve(
                new Response(
                    JSON.stringify({
                        token: "t",
                        model_id: "scribe_v3_live",
                        language_code: "es",
                        keyterms: ["café"],
                    }),
                ),
            ),
        ),
    );
    vi.stubGlobal("WebSocket", FakeScribe);
    vi.stubGlobal(
        "AudioContext",
        class {
            sampleRate: number;
            audioWorklet = { addModule: () => Promise.resolve() };
            constructor(options?: { sampleRate?: number }) {
                contextOptions.push(options);
                this.sampleRate = options?.sampleRate ?? 48_000;
            }
            createMediaStreamSource() {
                if (refuse16k && this.sampleRate !== 48_000) throw new Error("rate mismatch");
                return { connect: () => {} };
            }
            close() {
                return Promise.resolve();
            }
        },
    );
    vi.stubGlobal(
        "AudioWorkletNode",
        class {
            port = {
                set onmessage(listener: (event: { data: Float32Array }) => void) {
                    worklet = listener;
                },
            };
        },
    );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:capture");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const track = Object.assign(new EventTarget(), { stop: vi.fn() });
    Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
            getUserMedia: () =>
                Promise.resolve({ getTracks: () => [track], getAudioTracks: () => [track] }),
        },
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

async function opened(): Promise<{ speech: BrowserSpeech; scribe: FakeScribe }> {
    const speech = new BrowserSpeech("screen-1", "tok", (f) => heard.push(f));
    await speech.start();
    const scribe = FakeScribe.all.at(-1)!;
    scribe.readyState = FakeScribe.OPEN;
    return { speech, scribe };
}

it("asks Scribe for a 0.5 s VAD (default 1.5 s) and per-word confidence", async () => {
    const { speech, scribe } = await opened();
    const params = new URL(scribe.url).searchParams;
    expect(params.get("model_id")).toBe("scribe_v3_live");
    expect(params.get("commit_strategy")).toBe("vad");
    expect(params.get("vad_silence_threshold_secs")).toBe("0.5");
    expect(params.get("include_timestamps")).toBe("true");
    speech.stop();
});

it("captures at 16 kHz so the browser low-passes before decimating", async () => {
    const { speech } = await opened();
    expect(contextOptions[0]).toEqual({ sampleRate: 16_000 });
    speech.stop();
});

it("falls back to the device rate when the browser refuses a 16 kHz context", async () => {
    refuse16k = true;
    const { speech, scribe } = await opened();
    expect(contextOptions).toEqual([{ sampleRate: 16_000 }, undefined]);
    for (let i = 0; i < 48; i++) worklet?.({ data: new Float32Array(128).fill(0.1) });
    expect(scribe.sent).toHaveLength(1); // 48 blocks × 42.67 = 2048 samples
    speech.stop();
});

it("a settled turn in a crowd commits on the next chunk", async () => {
    const { speech, scribe } = await opened();
    const block = () => worklet?.({ data: new Float32Array(128).fill(0.05) });
    scribe.say({ message_type: "partial_transcript", text: "una chela" });
    for (let i = 0; i < 16; i++) block(); // 2048 samples at 16 kHz
    expect(scribe.sent.map((c) => c.commit)).toEqual([false]);
    vi.advanceTimersByTime(1_300); // babble keeps going, the partial stops growing
    for (let i = 0; i < 16; i++) block();
    for (let i = 0; i < 16; i++) block();
    expect(scribe.sent.map((c) => c.commit)).toEqual([false, true, false]);
    speech.stop();
});

it("a babble invention Scribe was unsure of never reaches the backend as a request", async () => {
    const { speech, scribe } = await opened();
    scribe.say({ message_type: "partial_transcript", text: "Un café" });
    scribe.say({ message_type: "committed_transcript", text: "Un café" });
    scribe.say({
        message_type: "committed_transcript_with_timestamps",
        text: "Un café",
        words: [
            { text: "Un", type: "word", logprob: -2.4 },
            { text: "café", type: "word", logprob: -1.9 },
        ],
    });
    expect(heard.map((u) => [u.is_final, u.text])).toEqual([
        [false, "Un café"],
        [true, ""],
    ]);
    speech.stop();
});

it("the resampler carries the fraction across blocks: 1 s at 48 kHz is exactly 16 000 samples", () => {
    const resampler = new Resampler(48_000);
    let total = 0;
    for (let i = 0; i < 375; i++) total += resampler.push(new Float32Array(128).fill(0.2)).length;
    expect(total).toBe(16_000);
});

it("the resampler low-passes: a 24 kHz tone (inaudible to the model) averages to ~silence", () => {
    const tone = new Float32Array(4_800).map((_, i) => (i % 2 === 0 ? 0.9 : -0.9));
    const out = new Resampler(48_000).push(tone);
    expect(Math.max(...Array.from(out, Math.abs))).toBeLessThan(0.35 * 0x7fff);
});
