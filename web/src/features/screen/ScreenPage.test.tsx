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
    send: vi.fn(),
}));
vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        constructor(options: { onMessage: (message: PlayerOutbound) => void }) {
            harness.receive = options.onMessage;
        }
        start() {}
        stop() {}
        send = harness.send;
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
    harness.send.mockClear();
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

function message(
    id: string,
    voice: string | null,
): Extract<PlayerOutbound, { type: "creative.ready" }> {
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
            perception: null,
            candidate_at: null,
            detected_at: null,
            published_at: null,
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
    it.each(["creative.ready", "creative.upgraded"] as const)(
        "ignores %s for another screen even when the creative id matches",
        async (type) => {
            await show("our-ad", null);
            const alien = message("our-ad", "/other-screen.mp3");
            alien.screen_id = "other-screen";
            alien.spec.slots[0]!.copy_text = "OTHER SCREEN OFFER";
            await act(async () => {
                harness.receive(
                    type === "creative.ready" ? alien : { ...alien, type, upgrade_kind: "copy" },
                );
                await Promise.resolve();
            });
            await act(async () => {
                await vi.advanceTimersByTimeAsync(250);
            });
            expect(visibleCopy()).toContain("our-ad");
            expect(visibleCopy()).not.toContain("OTHER SCREEN OFFER");
            expect(harness.prepare).not.toHaveBeenCalled();
        },
    );

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

    it("applies a copy upgrade immediately without decoding logos or replacing the mounted video", async () => {
        const ready = message("on-air", null);
        ready.spec.video_url = "/loop.mp4";
        await act(async () => {
            harness.receive(ready);
            await Promise.resolve();
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(250);
        });
        const video = host.querySelector('[aria-hidden="false"] video');
        const decode = vi.fn(() => Promise.reject(new Error("must reuse the verified logo")));
        vi.stubGlobal(
            "Image",
            class {
                decode = decode;
            },
        );
        const upgrade: PlayerOutbound = {
            ...ready,
            type: "creative.upgraded",
            upgrade_kind: "copy",
            spec: {
                ...ready.spec,
                slots: [{ ...ready.spec.slots[0]!, copy_text: "improved-now" }],
            },
        };
        await act(async () => {
            harness.receive(upgrade);
            await Promise.resolve();
        });
        expect(visibleCopy()).toContain("improved-now");
        expect(decode).not.toHaveBeenCalled();
        expect(host.querySelector('[aria-hidden="false"] video')).toBe(video);
    });

    it("preserves the dwell stage when copy is upgraded after the offer begins", async () => {
        const ready = message("dwell", null);
        ready.spec.stages = [
            { name: "teaser", copy_text: "teaser" },
            { name: "offer", copy_text: "original-offer" },
            { name: "close", copy_text: "close" },
        ];
        await act(async () => {
            harness.receive(ready);
            await Promise.resolve();
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(3250);
        });
        expect(visibleCopy()).toContain("original-offer");
        await act(async () => {
            harness.receive({
                ...ready,
                type: "creative.upgraded",
                upgrade_kind: "copy",
                spec: {
                    ...ready.spec,
                    stages: [
                        { name: "teaser", copy_text: "new-teaser" },
                        { name: "offer", copy_text: "new-offer" },
                        { name: "close", copy_text: "new-close" },
                    ],
                },
            });
            await Promise.resolve();
        });
        expect(visibleCopy()).toContain("new-offer");
        expect(visibleCopy()).not.toContain("new-teaser");
    });

    it("does not restart an in-flight logo decode when a warm upgrade arrives", async () => {
        let finish = () => {};
        const pending = new Promise<void>((resolve) => {
            finish = resolve;
        });
        const decode = vi.fn(() => pending);
        vi.stubGlobal(
            "Image",
            class {
                decode = decode;
            },
        );
        const ready = message("pending", null);
        await act(async () => {
            harness.receive(ready);
            await Promise.resolve();
        });
        await act(async () => {
            harness.receive({
                ...ready,
                type: "creative.upgraded",
                upgrade_kind: "copy",
                spec: {
                    ...ready.spec,
                    slots: [{ ...ready.spec.slots[0]!, copy_text: "latest-copy" }],
                },
            });
            await Promise.resolve();
        });
        expect(decode).toHaveBeenCalledTimes(1);
        await act(async () => {
            finish();
            await Promise.resolve();
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(visibleCopy()).toContain("latest-copy");
    });

    it("keeps the ad visible when audio fails", async () => {
        harness.prepare.mockRejectedValue(new Error("offline"));
        await show("silent-ad", "/broken.mp3");
        expect(visibleCopy()).toContain("silent-ad");
        expect(harness.play).not.toHaveBeenCalled();
    });
});

describe("glass-to-glass and attention-driven stages", () => {
    const displayedSends = () =>
        harness.send.mock.calls
            .map(([sent]) => sent as { type: string; creative_id: string; seq: number })
            .filter((sent) => sent.type === "player.displayed");

    function attention(looking: number, dwell: number): PlayerOutbound {
        return {
            type: "screen.attention",
            v: 1,
            screen_id: "demo-01",
            creative_id: null,
            looking,
            dwell_s: dwell,
            measured_at: new Date().toISOString(),
        };
    }

    it("sends player.displayed once per new creative, not on an in-place upgrade", async () => {
        harness.send.mockClear();
        const ready = message("shown", null);
        await show("shown", null);
        await act(async () => {
            harness.receive({ ...ready, type: "creative.upgraded", upgrade_kind: "copy" });
            await vi.advanceTimersByTimeAsync(500);
        });
        await show("next", null);
        expect(displayedSends().map((m) => [m.creative_id, m.seq])).toEqual([
            ["shown", 2],
            ["next", 3],
        ]);
    });

    it("follows attention instead of the timer, holding while nobody looks", async () => {
        const ready = message("dwell", null);
        ready.spec.stages = [
            { name: "teaser", copy_text: "teaser" },
            { name: "offer", copy_text: "offer" },
            { name: "close", copy_text: "close" },
        ];
        await act(async () => {
            harness.receive(ready);
            await vi.advanceTimersByTimeAsync(250);
        });
        for (let i = 0; i < 10; i++) {
            await act(async () => {
                harness.receive(attention(0, 0)); // nobody looking: hold
                await vi.advanceTimersByTimeAsync(1000);
            });
        }
        expect(visibleCopy()).toContain("teaser");
        await act(async () => {
            harness.receive(attention(2, 4));
            await vi.advanceTimersByTimeAsync(200);
        });
        expect(visibleCopy()).toContain("offer");
        await act(async () => {
            harness.receive(attention(2, 9));
            await vi.advanceTimersByTimeAsync(200);
        });
        expect(visibleCopy()).toContain("close");
        expect(visibleCopy()).toContain("El QR se emite al activar la campaña.");
    });

    it("renders the scannable QR, approved price, and code at the close stage", async () => {
        const spec = {
            ...HOUSE_AD.spec,
            stages: [{ name: "close" as const, copy_text: "Escanea" }],
            offer: {
                label: "Trío post-entreno",
                text: "Voltio + Café Patio x3",
                price: { amount: "8490", currency: "CLP" },
                party_size: 3,
            },
            qr_url: "data:image/svg+xml;base64,PHN2Zy8+",
            redemption_code: "AB12CD",
        };
        await act(async () => {
            root.render(<CreativeView spec={spec} playKey="qr" />);
            await Promise.resolve();
        });
        const qr = host.querySelector<HTMLImageElement>('img[alt="QR para canjear la oferta"]');
        expect(qr?.src).toBe(spec.qr_url);
        expect(host.querySelector(".creative-close .creative-price")?.textContent).toContain(
            "Para 3 · $8.490",
        );
        expect(host.textContent).toContain("AB12CD");
        expect(host.textContent).toContain("válido 10 min");
    });
});

it("starts optional voice preparation before the visual crossfade finishes", async () => {
    harness.prepare.mockReturnValue(new Promise<void>(() => {}));
    await act(async () => {
        harness.receive(message("prefetched", "/prefetched.mp3"));
        await Promise.resolve();
    });
    expect(harness.prepare).toHaveBeenCalledWith("/prefetched.mp3", expect.any(AbortSignal));
    expect(visibleCopy()).not.toContain("prefetched");
    expect(harness.play).not.toHaveBeenCalled();
    await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
    });
    expect(visibleCopy()).toContain("prefetched");
});
