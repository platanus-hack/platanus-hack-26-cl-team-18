import type { PlayerOutbound } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreativeView } from "./CreativeView";
import { HOUSE_AD } from "./house-ad";
import { ScreenPage } from "./ScreenPage";

const harness = vi.hoisted(() => ({
    receive: (_message: PlayerOutbound): void => {},
    prepare: vi.fn<(_url: string, _signal: AbortSignal) => Promise<void>>(),
    play: vi.fn(),
    stop: vi.fn(),
}));
vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        constructor(options: { onMessage: (message: PlayerOutbound) => void }) {
            harness.receive = options.onMessage;
        }
        start() {}
        stop() {}
        send() {}
    },
}));
vi.mock("./voice", () => ({
    VoicePlayer: class {
        prepare = harness.prepare;
        play = harness.play;
        stop = harness.stop;
        isReady() {
            return false;
        }
    },
}));

let root: Root;
let host: HTMLDivElement;
beforeEach(async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.stubGlobal(
        "Image",
        class {
            decode() {
                return Promise.resolve();
            }
        },
    );
    harness.prepare.mockReset();
    harness.play.mockClear();
    harness.stop.mockClear();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => {
        await Promise.resolve();
        root.render(<ScreenPage />);
    });
});
afterEach(async () => {
    await act(async () => {
        await Promise.resolve();
        root.unmount();
    });
    host.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

function message(id: string, voice: string | null): PlayerOutbound {
    return {
        type: "creative.ready",
        v: 1,
        screen_id: "demo-01",
        creative_id: id,
        auction_id: "auction",
        spec: {
            ...HOUSE_AD.spec,
            slots: [{ ...HOUSE_AD.spec.slots[0]!, copy_text: id }],
            voiceover_url: voice,
        },
        trace: {
            measured_at: new Date().toISOString(),
            received_at: new Date().toISOString(),
            cleared_at: null,
            ready_at: null,
            displayed_at: null,
            moment_changed_at: null,
            upgraded_at: null,
            swap_not_before: null,
        },
    };
}

async function show(id: string, voice: string | null) {
    await act(async () => {
        await Promise.resolve();
        harness.receive(message(id, voice));
    });
    await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
    });
}

function visibleCopy() {
    return host.querySelector('[aria-hidden="false"]')?.textContent;
}

describe("screen display and voice lifecycle", () => {
    it("shows an ad before slow audio is ready, then plays its late voice", async () => {
        let resolve = () => {};
        const audio = {
            promise: new Promise<void>((done) => {
                resolve = done;
            }),
            resolve: () => {
                resolve();
            },
        };
        harness.prepare.mockReturnValue(audio.promise);
        await show("new-ad", "/slow.mp3");
        expect(visibleCopy()).toContain("new-ad");
        expect(harness.play).not.toHaveBeenCalled();
        expect(harness.prepare).toHaveBeenCalledWith("/slow.mp3", expect.any(AbortSignal));
        await act(async () => {
            await Promise.resolve();
            audio.resolve();
        });
        expect(harness.play).toHaveBeenCalledExactlyOnceWith("new-ad", "/slow.mp3");
    });

    it("cancels a replaced ad's pending voice and ignores its late completion", async () => {
        let resolve = () => {};
        const audio = {
            promise: new Promise<void>((done) => {
                resolve = done;
            }),
            resolve: () => {
                resolve();
            },
        };
        harness.prepare.mockReturnValue(audio.promise);
        await show("old-ad", "/old.mp3");
        const signal = harness.prepare.mock.calls[0]![1];
        await show("replacement", null);
        expect(visibleCopy()).toContain("replacement");
        expect(signal.aborted).toBe(true);
        await act(async () => {
            await Promise.resolve();
            audio.resolve();
        });
        expect(harness.play).not.toHaveBeenCalled();
    });

    it("keeps the house ad on air when a required logo fails", async () => {
        vi.stubGlobal(
            "Image",
            class {
                decode() {
                    return Promise.reject(new Error("missing logo"));
                }
            },
        );
        vi.spyOn(console, "warn").mockImplementation(() => {});
        await show("broken-ad", null);
        expect(visibleCopy()).toContain("Esta pantalla");
        expect(visibleCopy()).not.toContain("broken-ad");
    });

    it("reveals optional video only after the mounted element can play", async () => {
        await act(async () => {
            root.render(
                <CreativeView
                    spec={{ ...HOUSE_AD.spec, video_url: "/loop.mp4" }}
                    playKey="video"
                />,
            );
            await Promise.resolve();
        });
        const video = host.querySelector("video")!;
        expect(video.style.opacity).toBe("0");
        expect(host.textContent).toContain("Esta pantalla");
        await act(async () => {
            video.dispatchEvent(new Event("canplaythrough"));
            await Promise.resolve();
        });
        expect(video.style.opacity).toBe("1");
        await act(async () => {
            video.dispatchEvent(new Event("error"));
            await Promise.resolve();
        });
        expect(video.style.opacity).toBe("0");
        expect(host.textContent).toContain("Esta pantalla");
    });

    it("keeps the ad visible when audio fails", async () => {
        harness.prepare.mockRejectedValue(new Error("offline"));
        await show("silent-ad", "/broken.mp3");
        expect(visibleCopy()).toContain("silent-ad");
        expect(harness.play).not.toHaveBeenCalled();
    });
});
