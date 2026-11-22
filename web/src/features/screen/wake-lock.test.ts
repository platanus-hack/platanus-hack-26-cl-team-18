import { describe, expect, it, vi } from "vitest";
import { holdScreenAwake, type WakeLockLike } from "./wake-lock";

function fakeLock() {
    const sentinels: { released: boolean; release: () => Promise<void> }[] = [];
    const request = vi.fn(() => {
        const sentinel = {
            released: false,
            release: vi.fn(() => {
                sentinel.released = true;
                return Promise.resolve();
            }),
        };
        sentinels.push(sentinel);
        return Promise.resolve(sentinel);
    });
    const lock: WakeLockLike = { request };
    return { lock, request, sentinels };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("holdScreenAwake", () => {
    it("requests a screen wake lock while the player is visible", async () => {
        const { lock, request } = fakeLock();
        const stop = holdScreenAwake(lock);
        await flush();
        expect(request).toHaveBeenCalledWith("screen");
        stop();
    });

    it("re-acquires after the browser drops the lock on a hidden page", async () => {
        const { lock, request, sentinels } = fakeLock();
        const stop = holdScreenAwake(lock);
        await flush();
        sentinels[0]!.released = true; // the browser releases it when the tab hides
        document.dispatchEvent(new Event("visibilitychange"));
        await flush();
        expect(request).toHaveBeenCalledTimes(2);
        stop();
    });

    it("does not stack locks while one is held", async () => {
        const { lock, request } = fakeLock();
        const stop = holdScreenAwake(lock);
        await flush();
        document.dispatchEvent(new Event("visibilitychange"));
        await flush();
        expect(request).toHaveBeenCalledTimes(1);
        stop();
    });

    it("releases on stop and stays a no-op without the API or on refusal", async () => {
        const { lock, sentinels } = fakeLock();
        const stop = holdScreenAwake(lock);
        await flush();
        stop();
        expect(sentinels[0]!.released).toBe(true);

        expect(() => {
            holdScreenAwake(undefined)();
        }).not.toThrow();
        const refused: WakeLockLike = { request: () => Promise.reject(new Error("denied")) };
        const stopRefused = holdScreenAwake(refused);
        await flush();
        stopRefused();
    });
});
