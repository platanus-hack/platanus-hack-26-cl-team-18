/**
 * Demo-day failures of the in-browser speech sensor (ADR 0005 + 0010): the token
 * endpoint is down, the mic is denied, Scribe sends junk or drops the session, a
 * loud hall clips the mic, standby lands mid-start. The browser APIs (fetch, mic,
 * AudioWorklet, WebSocket) are fakes; BrowserSpeech is the real code.
 */
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import { BrowserSpeech, toPcm16, type UtteranceFacts } from "./speech";

class FakeScribe {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;
    static all: FakeScribe[] = [];
    readyState = FakeScribe.CONNECTING;
    sent: { message_type: string; audio_base_64: string }[] = [];
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    constructor(readonly url: string) {
        FakeScribe.all.push(this);
    }
    send(raw: string) {
        this.sent.push(JSON.parse(raw) as FakeScribe["sent"][number]);
    }
    close() {
        this.readyState = FakeScribe.CLOSED;
    }
    /** Scribe → browser. */
    say(message: unknown) {
        this.onmessage?.({ data: typeof message === "string" ? message : JSON.stringify(message) });
    }
    /** Server or network drops the session. */
    drop() {
        this.readyState = FakeScribe.CLOSED;
        this.onclose?.();
    }
}

let worklet: ((event: { data: Float32Array }) => void) | null;
let micTracks: (EventTarget & { stop: ReturnType<typeof vi.fn> })[];
let fetchMock: ReturnType<typeof vi.fn>;
let getUserMedia: Mock<(constraints?: unknown) => Promise<unknown>>;
let heard: UtteranceFacts[];

const session = {
    token: "single-use",
    model_id: "scribe_v2_realtime",
    language_code: "es",
    keyterms: ["café", "Red Bull"],
};

beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    FakeScribe.all = [];
    worklet = null;
    micTracks = [];
    heard = [];
    fetchMock = vi.fn(() =>
        Promise.resolve(new Response(JSON.stringify(session), { status: 200 })),
    );
    getUserMedia = vi.fn((_constraints?: unknown): Promise<unknown> => {
        const track = Object.assign(new EventTarget(), { stop: vi.fn() });
        micTracks.push(track);
        return Promise.resolve({ getTracks: () => [track], getAudioTracks: () => [track] });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("WebSocket", FakeScribe);
    vi.stubGlobal(
        "AudioContext",
        class {
            sampleRate = 48_000;
            audioWorklet = { addModule: () => Promise.resolve() };
            createMediaStreamSource() {
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
    Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia: (c: unknown) => getUserMedia(c) },
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function sensor(): BrowserSpeech {
    return new BrowserSpeech("screen-1", "screen-token", (facts) => heard.push(facts));
}

async function opened(): Promise<{ speech: BrowserSpeech; scribe: FakeScribe }> {
    const speech = sensor();
    await speech.start();
    const scribe = FakeScribe.all.at(-1);
    if (scribe === undefined) throw new Error("Scribe never opened");
    scribe.readyState = FakeScribe.OPEN;
    return { speech, scribe };
}

/** 128-sample worklet blocks at 48 kHz, like the real capture node posts. */
function micBlocks(count: number, sample = 0.1) {
    for (let i = 0; i < count; i++) worklet?.({ data: new Float32Array(128).fill(sample) });
}

describe("BrowserSpeech on a demo day", () => {
    it("asks the backend for a token (the API key never reaches the browser) and opens Scribe with keyterms", async () => {
        const { speech, scribe } = await opened();
        const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
        expect(url).toMatch(/\/api\/speech\/token\/screen-1$/);
        expect((init.headers as Record<string, string>)["Authorization"]).toBe(
            "Bearer screen-token",
        );
        expect(scribe.url).toContain("token=single-use");
        expect(scribe.url).toContain("keyterms=Red+Bull");
        expect(scribe.url).not.toMatch(/xi-api-key|api_key/i);
        speech.stop();
    });

    it("a token endpoint that is down neither throws nor hammers: it waits 10 s to retry", async () => {
        fetchMock.mockImplementation(() => Promise.resolve(new Response("", { status: 503 })));
        const speech = sensor();
        await expect(speech.start()).resolves.toBeUndefined();
        expect(getUserMedia).not.toHaveBeenCalled();
        expect(FakeScribe.all).toHaveLength(0);

        // live.status and every snapshot call start() again: still only one request.
        for (let i = 0; i < 20; i++) await speech.start();
        expect(fetchMock).toHaveBeenCalledTimes(1);

        fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(session))));
        await vi.advanceTimersByTimeAsync(10_001);
        await speech.start();
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(FakeScribe.all).toHaveLength(1);
        speech.stop();
    });

    it("a network error on the token fetch backs off the same way", async () => {
        fetchMock.mockImplementation(() => Promise.reject(new TypeError("Failed to fetch")));
        const speech = sensor();
        await speech.start();
        await speech.start();
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("a denied or busy mic opens no Scribe session (no billed silence) and backs off", async () => {
        getUserMedia.mockImplementation(() =>
            Promise.reject(new DOMException("in use", "NotReadableError")),
        );
        const speech = sensor();
        await speech.start();
        expect(FakeScribe.all).toHaveLength(0);
        await speech.start();
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("Scribe junk (non-JSON, unknown types, errors, blank partials) is never an utterance", async () => {
        const { speech, scribe } = await opened();
        scribe.say("not json {");
        scribe.say("");
        scribe.say({ message_type: "session_started" });
        scribe.say({ message_type: "partial_transcript", text: "" });
        scribe.say({ message_type: "partial_transcript", text: "   \n " });
        scribe.say({ message_type: "partial_transcript" });
        scribe.say({ message_type: "committed_transcript", text: "  " });
        scribe.say({ message_type: "quota_exceeded_error", text: "no credits" });
        scribe.say({ message_type: "auth_error" });
        scribe.say({ text: "no type at all" });
        scribe.say(42);
        scribe.say(null);
        scribe.say({ message_type: "partial_transcript", text: 42 });
        scribe.say({ message_type: "committed_transcript", text: { nested: true } });
        expect(heard).toHaveLength(0);
        speech.stop();
    });

    it("partials stream, a commit closes the utterance, and the next one gets a new id", async () => {
        const { speech, scribe } = await opened();
        scribe.say({ message_type: "partial_transcript", text: "un ca" });
        scribe.say({ message_type: "partial_transcript", text: "un café" });
        scribe.say({ message_type: "committed_transcript", text: " un café por favor " });
        scribe.say({
            message_type: "committed_transcript_with_timestamps",
            text: "un café por favor",
            words: [{ text: "un", type: "word", logprob: -0.4 }],
        });
        scribe.say({ message_type: "partial_transcript", text: "y un red bull" });
        const session = heard[0]!.utterance_id!.split(":")[0];
        expect(heard.map((u) => [u.utterance_id, u.is_final, u.text])).toEqual([
            [`${session}:0`, false, "un ca"],
            [`${session}:0`, false, "un café"],
            [`${session}:0`, true, "un café por favor"],
            [`${session}:1`, false, "y un red bull"],
        ]);
        expect(heard.every((u) => u.language === "es")).toBe(true);
        expect(heard.some((u) => u.partials_stabilized)).toBe(false);
        speech.stop();
    });

    it("a hall-noise run-on transcript is capped at 1000 chars before it leaves the page", async () => {
        const { speech, scribe } = await opened();
        scribe.say({ message_type: "partial_transcript", text: "bla ".repeat(5_000) });
        expect(heard[0]?.text.length).toBeLessThanOrEqual(1_000);
        speech.stop();
    });

    it("a dropped Scribe session reopens by itself with a fresh single-use token", async () => {
        const { speech, scribe } = await opened();
        scribe.drop();
        expect(micTracks[0]?.stop).toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(1_100);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(FakeScribe.all).toHaveLength(2);
        FakeScribe.all[1]!.readyState = FakeScribe.OPEN;
        FakeScribe.all[1]!.say({ message_type: "partial_transcript", text: "hola" });
        expect(heard.at(-1)?.text).toBe("hola");
        speech.stop();
    });

    it("a close we caused (standby) does not reopen Scribe", async () => {
        const { speech, scribe } = await opened();
        speech.stop();
        scribe.drop(); // the browser fires close after our own close()
        await vi.advanceTimersByTimeAsync(5_000);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(FakeScribe.all).toHaveLength(1);
    });

    it("standby during the token fetch releases the mic and never opens Scribe", async () => {
        let answer: (r: Response) => void = () => {};
        fetchMock.mockImplementation(
            () =>
                new Promise<Response>((resolve) => {
                    answer = resolve;
                }),
        );
        const speech = sensor();
        const starting = speech.start();
        speech.stop();
        answer(new Response(JSON.stringify(session)));
        await starting;
        expect(FakeScribe.all).toHaveLength(0);
        for (const track of micTracks) expect(track.stop).toHaveBeenCalled();
    });

    it("audio while Scribe is still connecting is dropped, never queued and replayed late", async () => {
        const speech = sensor();
        await speech.start();
        const scribe = FakeScribe.all[0]!;
        micBlocks(200); // ~0.5 s of speech before the socket opens
        expect(scribe.sent).toHaveLength(0);
        scribe.readyState = FakeScribe.OPEN;
        // Each 128-sample block at 48 kHz → 42.67 samples at 16 kHz (the fraction carries
        // over, nothing is dropped); 48 blocks are exactly 2048.
        micBlocks(48);
        expect(scribe.sent).toHaveLength(1);
        const bytes = atob(scribe.sent[0]!.audio_base_64).length;
        expect(bytes).toBe(2 * 2048); // only post-open audio, nothing from before
        speech.stop();
    });

    it("a clipping mic (shouting, feedback) still produces valid in-range PCM chunks", async () => {
        const { speech, scribe } = await opened();
        micBlocks(49, 7.5);
        expect(scribe.sent).toHaveLength(1);
        expect(scribe.sent[0]?.message_type).toBe("input_audio_chunk");
        const raw = atob(scribe.sent[0]!.audio_base_64);
        const first = (raw.charCodeAt(1) << 8) | raw.charCodeAt(0); // little-endian PCM16
        expect(first).toBe(0x7fff); // clamped to full scale, not wrapped negative
        speech.stop();
    });

    it("start() while already running opens one session, not two", async () => {
        const speech = sensor();
        await Promise.all([speech.start(), speech.start(), speech.start()]);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(FakeScribe.all).toHaveLength(1);
        speech.stop();
    });

    it("an unplugged or revoked mic (track ended) releases Scribe, and the next start reopens", async () => {
        const { speech, scribe } = await opened();
        micTracks[0]?.dispatchEvent(new Event("ended"));
        expect(scribe.readyState).toBe(FakeScribe.CLOSED);
        await speech.start(); // the next snapshot asks again
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(FakeScribe.all).toHaveLength(2);
        speech.stop();
    });

    it("standby then live again mid-token-fetch: one Scribe session and one open mic", async () => {
        let answer: (r: Response) => void = () => {};
        fetchMock.mockImplementationOnce(
            () =>
                new Promise<Response>((resolve) => {
                    answer = resolve;
                }),
        );
        const speech = sensor();
        const first = speech.start();
        speech.stop(); // the room emptied
        await speech.start(); // someone came back
        answer(new Response(JSON.stringify(session)));
        await first;
        await vi.advanceTimersByTimeAsync(0);
        expect(FakeScribe.all).toHaveLength(1);
        expect(micTracks.filter((t) => t.stop.mock.calls.length === 0)).toHaveLength(1);
        speech.stop();
        for (const track of micTracks) expect(track.stop).toHaveBeenCalled();
    });

    it("a Scribe drop followed by standby never reopens the mic", async () => {
        const { speech, scribe } = await opened();
        scribe.drop();
        speech.stop(); // standby lands before the 1 s reopen
        await vi.advanceTimersByTimeAsync(5_000);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(getUserMedia).toHaveBeenCalledTimes(1);
    });

    it("retryNow skips the backoff once the mic is allowed again", async () => {
        getUserMedia.mockImplementationOnce(() =>
            Promise.reject(new DOMException("denied", "NotAllowedError")),
        );
        const speech = sensor();
        await speech.start();
        await speech.start(); // inside the 10 s backoff: nothing
        expect(getUserMedia).toHaveBeenCalledTimes(1);
        speech.retryNow();
        await speech.start();
        expect(getUserMedia).toHaveBeenCalledTimes(2);
        expect(FakeScribe.all).toHaveLength(1);
        speech.stop();
    });
});

describe("toPcm16 under hall noise", () => {
    it("clamps overdriven samples instead of wrapping around", () => {
        const pcm = toPcm16(new Float32Array([5, -5, 1.0001, -40, 1, -1]), 16_000);
        expect(Array.from(pcm)).toEqual([32767, -32768, 32767, -32768, 32767, -32768]);
    });

    it("a NaN from a glitching driver becomes silence, never garbage", () => {
        const pcm = toPcm16(new Float32Array([0.5, NaN, 0.5, 0.5]), 16_000);
        expect(Array.from(pcm).every((s) => Number.isInteger(s) && Math.abs(s) <= 32768)).toBe(
            true,
        );
        expect(pcm[1]).toBe(0);
    });

    it.each([44_100, 48_000, 96_000, 16_000, 8_000])("resamples %i Hz to 16 kHz length", (rate) => {
        const out = toPcm16(new Float32Array(rate), rate); // one second
        expect(Math.abs(out.length - 16_000)).toBeLessThanOrEqual(1);
    });

    it("an empty block is an empty chunk, not a crash", () => {
        expect(toPcm16(new Float32Array(0), 48_000)).toHaveLength(0);
    });
});
