import type { PlayerOutbound } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as AccentModule from "./accent";
import { CreativeView } from "./CreativeView";
import { HOUSE_AD } from "./house-ad";
import type * as PlayerMachineModule from "./player-machine";
import { PlayerCrashed, ScreenPage } from "./ScreenPage";

/**
 * Live-demo failure modes: whatever a creative does at render time, the TV
 * keeps showing an ad (the house ad or the last good creative). Never blank.
 */

const CRASH_ACCENT = "#000001";
const harness = vi.hoisted(() => ({
    receive: (_message: PlayerOutbound): void => {},
    playerBroken: false,
}));
// A render-time bug in the player shell itself (outside any one creative).
// The page's own sensor (camera/mic, ADR 0010) is covered in sensor/*.test.ts.
vi.mock("./sensor/link", () => ({
    sensingEnabled: () => false,
    ScreenSensor: class {
        start(): void {}
        stop(): void {}
    },
}));

vi.mock("./player-machine", async (original) => {
    const real = await original<typeof PlayerMachineModule>();
    return {
        ...real,
        frontCreative: (state: Parameters<typeof real.frontCreative>[0]) => {
            if (harness.playerBroken) throw new Error("player bug");
            return real.frontCreative(state);
        },
    };
});
vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        constructor(options: { onMessage: (message: PlayerOutbound) => void }) {
            harness.receive = options.onMessage;
        }
        start() {}
        stop() {}
        send() {
            return false;
        }
    },
}));
vi.mock("./voice", () => ({
    VoicePlayer: class {
        prepare() {
            return Promise.resolve();
        }
        play() {}
        stop() {}
        isReady() {
            return false;
        }
    },
}));
// A render-time bug inside the creative: any spec with this accent throws.
vi.mock("./accent", async (original) => {
    const real = await original<typeof AccentModule>();
    return {
        ...real,
        readableAccent: (hex: string | null | undefined) => {
            if (hex === CRASH_ACCENT) throw new Error("render bug");
            return real.readableAccent(hex);
        },
    };
});

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
    vi.spyOn(console, "error").mockImplementation(() => {});
    harness.playerBroken = false;
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

function ready(id: string, accent: string | null = "#c6f26b"): PlayerOutbound {
    return {
        type: "creative.ready",
        v: 1,
        screen_id: "demo-01",
        creative_id: id,
        auction_id: "auction",
        spec: {
            ...HOUSE_AD.spec,
            slots: [{ ...HOUSE_AD.spec.slots[0]!, copy_text: id, accent_color: accent }],
        },
        trace: {
            perception: null,
            candidate_at: null,
            detected_at: null,
            published_at: null,
            measured_at: null,
            received_at: null,
            cleared_at: null,
            ready_at: null,
            displayed_at: null,
            moment_changed_at: null,
            upgraded_at: null,
            swap_not_before: null,
        },
    };
}

async function show(message: PlayerOutbound) {
    await act(async () => {
        await Promise.resolve();
        harness.receive(message);
    });
    await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
    });
}

const visible = () => host.querySelector('[aria-hidden="false"]');

describe("a creative that throws while rendering", () => {
    it("falls back to the house ad instead of blanking the TV", async () => {
        await show(ready("crashing-ad", CRASH_ACCENT));
        expect(host.querySelector("main")).not.toBeNull();
        expect(visible()?.textContent).toContain("Acércate");
    });

    it("the next good creative still plays after a crash", async () => {
        await show(ready("crashing-ad", CRASH_ACCENT));
        await show(ready("good-ad"));
        expect(visible()?.textContent).toContain("good-ad");
        expect(visible()?.getAttribute("data-creative-id")).toBe("good-ad");
    });
});

describe("the player shell throws", () => {
    it("shows the house ad, not an error text, then reloads the kiosk", async () => {
        harness.playerBroken = true;
        await show(ready("any-ad"));
        expect(host.textContent).toContain("Acércate");
        expect(host.textContent).not.toContain("No se pudo mostrar");

        const reload = vi.fn();
        await act(async () => {
            root.render(<PlayerCrashed reload={reload} />);
            await Promise.resolve();
        });
        expect(host.textContent).toContain("Acércate");
        await act(async () => {
            await vi.advanceTimersByTimeAsync(10_000);
        });
        expect(reload).toHaveBeenCalledOnce();
    });
});

describe("long-run resources", () => {
    it("releases a video loop's decoder when its creative leaves the screen", async () => {
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
        const load = vi.spyOn(video, "load").mockImplementation(() => {});
        expect(video.getAttribute("src")).toContain("/loop.mp4");
        await act(async () => {
            root.render(<CreativeView spec={HOUSE_AD.spec} playKey="house" />);
            await Promise.resolve();
        });
        expect(video.hasAttribute("src")).toBe(false);
        expect(load).toHaveBeenCalled();
    });
});
