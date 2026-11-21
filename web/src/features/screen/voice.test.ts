import { describe, expect, it, vi } from "vitest";
import { VoicePlayer } from "./voice";

function fakeContext() {
    const started: AudioBuffer[] = [];
    const stopped: number[] = [];
    const context = {
        state: "running",
        destination: {},
        resume: vi.fn(),
        createGain: () => ({ gain: { value: 1 }, connect: vi.fn() }),
        decodeAudioData: vi.fn((bytes: ArrayBuffer) =>
            Promise.resolve({ duration: bytes.byteLength } as unknown as AudioBuffer),
        ),
        createBufferSource: () => {
            const node = {
                buffer: null as AudioBuffer | null,
                connect: vi.fn(),
                start: () => started.push(node.buffer!),
                stop: () => stopped.push(1),
            };
            return node;
        },
    };
    return { context: context as unknown as AudioContext, started, stopped, raw: context };
}

const ok = (bytes = 8) => Promise.resolve(new Response(new Uint8Array(bytes)));

describe("VoicePlayer", () => {
    it("decodes during preload and plays once per creative", async () => {
        const { context, started } = fakeContext();
        const fetcher = vi.fn(() => ok());
        const voice = new VoicePlayer(() => context, fetcher);
        const signal = new AbortController().signal;
        await voice.prepare("/media/voiceovers/a.mp3", signal);
        await voice.prepare("/media/voiceovers/a.mp3", signal); // cached
        expect(fetcher).toHaveBeenCalledTimes(1);
        expect(voice.play("cr-1", "/media/voiceovers/a.mp3")).toBe(true);
        expect(voice.play("cr-1", "/media/voiceovers/a.mp3")).toBe(false); // no repeat
        expect(voice.play("cr-2", "/media/voiceovers/a.mp3")).toBe(true); // new creative
        expect(started).toHaveLength(2);
    });

    it("does nothing for a voice that never loaded", () => {
        const { context } = fakeContext();
        const voice = new VoicePlayer(
            () => context,
            vi.fn(() => ok()),
        );
        expect(voice.play("cr-1", "/media/voiceovers/missing.mp3")).toBe(false);
    });

    it("rejects on HTTP errors so preload can treat the voice as optional", async () => {
        const { context } = fakeContext();
        const voice = new VoicePlayer(
            () => context,
            vi.fn(() => Promise.resolve(new Response("nope", { status: 404 }))),
        );
        await expect(voice.prepare("/media/x.mp3", new AbortController().signal)).rejects.toThrow(
            "404",
        );
        expect(voice.isReady("/media/x.mp3")).toBe(false);
    });

    it("stops the previous line when a new one starts", async () => {
        const { context, stopped } = fakeContext();
        const voice = new VoicePlayer(
            () => context,
            vi.fn(() => ok()),
        );
        const signal = new AbortController().signal;
        await voice.prepare("/a.mp3", signal);
        await voice.prepare("/b.mp3", signal);
        voice.play("cr-1", "/a.mp3");
        voice.play("cr-2", "/b.mp3");
        expect(stopped).toHaveLength(1);
    });
});
