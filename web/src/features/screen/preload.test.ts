import type { CreativeSpec } from "@/contract";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HOUSE_AD } from "./house-ad";
import { frontCreative, initialPlayerState, playerReducer } from "./player-machine";
import { preloadCreative } from "./preload";

const spec: CreativeSpec = {
    ...HOUSE_AD.spec,
    slots: [{ ...HOUSE_AD.spec.slots[0]!, logo_url: "/logo.svg", hero_image_url: "/slow.png" }],
    video_url: "/slow.mp4",
    voiceover_url: "/slow.mp3",
};

beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
});
afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function images(decode: (url: string) => Promise<void>) {
    const calls: string[] = [];
    vi.stubGlobal(
        "Image",
        class {
            src = "";
            decode() {
                calls.push(this.src);
                return decode(this.src);
            }
        },
    );
    return calls;
}

describe("visual readiness", () => {
    it("finishes the display swap in 250 ms even when optional media never loads", async () => {
        images((url) =>
            url.endsWith("logo.svg") ? Promise.resolve() : new Promise(() => undefined),
        );
        const controller = new AbortController();
        let state = playerReducer(initialPlayerState(HOUSE_AD, 0), {
            type: "received",
            creative: { ...HOUSE_AD, creativeId: "new", spec },
            at: 0,
        });
        let ready = false;
        const pending = preloadCreative(spec, controller.signal).then(() => {
            ready = true;
            state = playerReducer(state, { type: "preloaded", creativeId: "new", at: Date.now() });
            setTimeout(() => {
                state = playerReducer(state, {
                    type: "crossfadeDone",
                    creativeId: "new",
                    at: Date.now(),
                });
            }, 250);
        });
        await vi.advanceTimersByTimeAsync(250);
        expect(ready).toBe(true);
        expect(frontCreative(state).creativeId).toBe("new");
        controller.abort();
        await pending;
    });

    it("keeps the last good ad when a required logo fails", async () => {
        images(() => Promise.reject(new Error("broken logo")));
        await expect(preloadCreative(spec, new AbortController().signal)).rejects.toThrow(
            "broken logo",
        );
    });

    it("bounds a stalled required logo", async () => {
        images(() => new Promise(() => undefined));
        const result = preloadCreative(spec, new AbortController().signal);
        const checked = expect(result).rejects.toThrow("timed out");
        await vi.advanceTimersByTimeAsync(1500);
        await checked;
    });

    it("releases timeout and abort listeners when superseded during a decode", async () => {
        images(() => new Promise(() => undefined));
        const controller = new AbortController();
        const remove = vi.spyOn(controller.signal, "removeEventListener");
        const checked = expect(preloadCreative(HOUSE_AD.spec, controller.signal)).rejects.toThrow(
            "aborted",
        );
        controller.abort();
        await checked;
        expect(vi.getTimerCount()).toBe(0);
        expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    });

    it("decodes a shared logo once per creative", async () => {
        const calls = images(() => Promise.resolve());
        await preloadCreative(
            { ...HOUSE_AD.spec, slots: [spec.slots[0]!, spec.slots[0]!] },
            new AbortController().signal,
        );
        expect(calls.filter((url) => url.endsWith("logo.svg"))).toHaveLength(1);
    });

    it("does no work for a preload already superseded", async () => {
        const calls = images(() => Promise.resolve());
        const controller = new AbortController();
        controller.abort();
        await expect(preloadCreative(HOUSE_AD.spec, controller.signal)).rejects.toThrow("aborted");
        expect(calls).toHaveLength(0);
    });
});
