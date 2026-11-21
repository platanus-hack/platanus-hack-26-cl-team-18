import { describe, expect, it } from "vitest";
import type { Playable } from "./player-machine";
import { frontCreative, initialPlayerState, playerReducer } from "./player-machine";

const spec = {
    layout_id: "solo",
    slots: [
        {
            brand_id: "b1",
            brand_name: "Voltio",
            logo_url: "/kits/voltio.svg",
            copy_text: "Dale.",
            accent_color: null,
            hero_image_url: null,
        },
    ],
    duration_ms: 8000,
    video_url: null,
    voiceover_url: null,
    qr_url: null,
};
const house: Playable = { creativeId: "house", auctionId: null, spec, kind: "house" };
const ad = (id: string): Playable => ({
    creativeId: id,
    auctionId: `au-${id}`,
    spec,
    kind: "auction",
});

describe("player machine", () => {
    it("starts on the house ad: never blank", () => {
        const s = initialPlayerState(house, 0);
        expect(frontCreative(s).creativeId).toBe("house");
        expect(s.phase.kind).toBe("showing");
    });

    it("preloads in the back layer, crossfades, then swaps", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 10 });
        expect(s.layers.b?.creativeId).toBe("c1");
        expect(frontCreative(s).creativeId).toBe("house"); // still showing the old one
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 20 });
        expect(s.phase).toEqual({ kind: "crossfading", creativeId: "c1" });
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 270 });
        expect(s.front).toBe("b");
        expect(frontCreative(s).creativeId).toBe("c1");
        expect(s.layers.a).toBeNull();
        expect(s.completed).toBeNull(); // house ads are not reported
    });

    it("latest wins while preloading; a stale preload result is ignored", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1 });
        s = playerReducer(s, { type: "received", creative: ad("c2"), at: 2 });
        const stale = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 3 });
        expect(stale).toBe(s);
        expect(s.layers.b?.creativeId).toBe("c2");
    });

    it("a failed preload keeps the current creative on screen", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1 });
        s = playerReducer(s, { type: "preloadFailed", creativeId: "c1", at: 2 });
        expect(frontCreative(s).creativeId).toBe("house");
        expect(s.phase.kind).toBe("showing");
        expect(s.layers.b).toBeNull();
    });

    it("queues only the newest creative during a crossfade and loads it after", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 2 });
        s = playerReducer(s, { type: "received", creative: ad("c2"), at: 3 });
        s = playerReducer(s, { type: "received", creative: ad("c3"), at: 4 });
        expect(s.queued?.creativeId).toBe("c3");
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 250 });
        expect(frontCreative(s).creativeId).toBe("c1");
        expect(s.phase).toEqual({ kind: "preloading", creativeId: "c3" });
    });

    it("reports a creative's play window when it leaves the screen", () => {
        let s = initialPlayerState(house, 0);
        for (const [id, t] of [
            ["c1", 100],
            ["c2", 9000],
        ] as const) {
            s = playerReducer(s, { type: "received", creative: ad(id), at: t });
            s = playerReducer(s, { type: "preloaded", creativeId: id, at: t });
            s = playerReducer(s, { type: "crossfadeDone", creativeId: id, at: t + 250 });
        }
        expect(s.completed).toEqual({ seq: 1, creativeId: "c1", startedAt: 350, endedAt: 9250 });
    });

    it("an in-place upgrade of the same creative keeps its play window running", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 0 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 0 });
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 250 });
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1000 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 1100 });
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 1350 });
        expect(s.completed).toBeNull();
        expect(s.shownSince).toBe(250);
    });
});
