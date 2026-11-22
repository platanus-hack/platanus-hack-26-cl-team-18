import { describe, expect, it, vi } from "vitest";
import { isExitKey, isFullscreenKey, toggleFullscreen } from "./fullscreen";

const key = (init: KeyboardEventInit) => new KeyboardEvent("keydown", init);

describe("fullscreen shortcut", () => {
    it("toggles on a plain F only", () => {
        expect(isFullscreenKey(key({ key: "f" }))).toBe(true);
        expect(isFullscreenKey(key({ key: "F" }))).toBe(true);
        expect(isFullscreenKey(key({ key: "f", ctrlKey: true }))).toBe(false);
        expect(isFullscreenKey(key({ key: "f", repeat: true }))).toBe(false);
        expect(isFullscreenKey(key({ key: "g" }))).toBe(false);
    });

    it("treats Esc as exit only", () => {
        expect(isExitKey(key({ key: "Escape" }))).toBe(true);
        expect(isExitKey(key({ key: "f" }))).toBe(false);
    });

    it("enters when windowed and exits when fullscreen", () => {
        const requestFullscreen = vi.fn(() => Promise.resolve());
        const exitFullscreen = vi.fn(() => Promise.resolve());
        const doc = (fullscreenElement: object | null) =>
            ({
                fullscreenElement,
                exitFullscreen,
                documentElement: { requestFullscreen },
            }) as unknown as Document;
        toggleFullscreen(doc(null));
        expect(requestFullscreen).toHaveBeenCalledOnce();
        toggleFullscreen(doc({}));
        expect(exitFullscreen).toHaveBeenCalledOnce();
    });
});
