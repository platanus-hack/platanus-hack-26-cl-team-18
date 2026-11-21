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
    stages: [],
    offer: null,
    angle: null,
    copy_source: null,
    redemption_code: null,
    redeem_url: null,
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
        expect(s.phase).toMatchObject({ kind: "preloading", creativeId: "c3" });
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

describe("displayed (glass-to-glass)", () => {
    const show = (s: ReturnType<typeof initialPlayerState>, creative: Playable, t: number) => {
        s = playerReducer(s, { type: "received", creative, at: t });
        s = playerReducer(s, { type: "preloaded", creativeId: creative.creativeId, at: t + 1 });
        return playerReducer(s, {
            type: "crossfadeDone",
            creativeId: creative.creativeId,
            at: t + 251,
        });
    };

    it("the first creative shown (the house ad) is displayed at start", () => {
        const s = initialPlayerState(house, 5);
        expect(s.displayed).toEqual({ seq: 1, creativeId: "house", displayedAt: 5 });
    });

    it("each new creative is displayed once, at crossfade done, with an increasing seq", () => {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 10 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 20 });
        expect(s.displayed.creativeId).toBe("house"); // not visible until the fade ends
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 270 });
        expect(s.displayed).toEqual({ seq: 2, creativeId: "c1", displayedAt: 270 });
        s = show(s, { ...house, creativeId: "house-2" }, 1000); // house ads count too
        expect(s.displayed).toEqual({ seq: 3, creativeId: "house-2", displayedAt: 1251 });
    });

    it("an in-place upgrade of the same creative does not re-display it", () => {
        let s = show(initialPlayerState(house, 0), ad("c1"), 0);
        const displayed = s.displayed;
        s = playerReducer(s, { type: "upgraded", creative: ad("c1"), at: 1000 });
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1100 });
        expect(s.displayed).toBe(displayed);
    });
});

describe("progressive upgrades", () => {
    const upgraded = (id: string): Playable => ({
        ...ad(id),
        spec: {
            ...spec,
            slots: [{ ...spec.slots[0]!, copy_text: "Fresh offer" }],
            voiceover_url: "/voice.mp3",
        },
    });
    function showing() {
        let s = initialPlayerState(house, 0);
        s = playerReducer(s, { type: "received", creative: ad("c1"), at: 1 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c1", at: 2 });
        return playerReducer(s, { type: "crossfadeDone", creativeId: "c1", at: 252 });
    }

    it("updates visible copy and voice immediately without a new play or crossfade", () => {
        const before = showing();
        const s = playerReducer(before, { type: "upgraded", creative: upgraded("c1"), at: 1000 });
        expect(s.phase.kind).toBe("showing");
        expect(s.front).toBe(before.front);
        expect(frontCreative(s).spec.voiceover_url).toBe("/voice.mp3");
        expect(s.shownSince).toBe(before.shownSince);
        expect(s.completed).toBeNull();
    });

    it("does not replace a newer queued ad with an upgrade to the fading ad", () => {
        let s = showing();
        s = playerReducer(s, { type: "received", creative: ad("c2"), at: 1000 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c2", at: 1001 });
        s = playerReducer(s, { type: "received", creative: ad("c3"), at: 1002 });
        s = playerReducer(s, { type: "upgraded", creative: upgraded("c2"), at: 1003 });
        expect(s.queued?.creativeId).toBe("c3");
        expect(s.layers[s.front === "a" ? "b" : "a"]?.spec.voiceover_url).toBe("/voice.mp3");
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c2", at: 1251 });
        expect(frontCreative(s).creativeId).toBe("c2");
        expect(s.phase).toMatchObject({ kind: "preloading", creativeId: "c3" });
    });

    it("keeps a pending creative's upgrade and reports one continuous prior play", () => {
        let s = showing();
        s = playerReducer(s, { type: "received", creative: ad("c2"), at: 1000 });
        s = playerReducer(s, { type: "upgraded", creative: upgraded("c2"), at: 1001 });
        expect(s.phase).toMatchObject({ kind: "preloading", creativeId: "c2" });
        expect(frontCreative(s).creativeId).toBe("c1");
        s = playerReducer(s, { type: "preloaded", creativeId: "c2", at: 1002 });
        s = playerReducer(s, { type: "crossfadeDone", creativeId: "c2", at: 1252 });
        expect(frontCreative(s).spec.voiceover_url).toBe("/voice.mp3");
        expect(s.completed).toEqual({ seq: 1, creativeId: "c1", startedAt: 252, endedAt: 1252 });
    });

    it("ignores stale upgrades instead of bringing retired ads back", () => {
        const before = showing();
        expect(
            playerReducer(before, { type: "upgraded", creative: upgraded("retired"), at: 1000 }),
        ).toBe(before);
    });

    it("does not apply an upgrade that changes required logos or auction ownership", () => {
        const before = showing();
        for (const creative of [
            { ...upgraded("c1"), auctionId: "different-auction" },
            {
                ...upgraded("c1"),
                spec: { ...spec, slots: [{ ...spec.slots[0]!, logo_url: "/unverified.svg" }] },
            },
        ]) {
            expect(playerReducer(before, { type: "upgraded", creative, at: 1000 })).toBe(before);
        }
    });

    it("refreshes a queued creative without losing the current transition", () => {
        let s = showing();
        s = playerReducer(s, { type: "received", creative: ad("c2"), at: 1000 });
        s = playerReducer(s, { type: "preloaded", creativeId: "c2", at: 1001 });
        s = playerReducer(s, { type: "received", creative: ad("c3"), at: 1002 });
        s = playerReducer(s, { type: "upgraded", creative: upgraded("c3"), at: 1003 });
        expect(s.phase).toEqual({ kind: "crossfading", creativeId: "c2" });
        expect(s.queued?.spec.voiceover_url).toBe("/voice.mp3");
    });

    it("a repeated ready message for the on-air creative does not restart its video layer", () => {
        const before = showing();
        const s = playerReducer(before, { type: "received", creative: ad("c1"), at: 1000 });
        expect(s.phase.kind).toBe("showing");
        expect(s.front).toBe(before.front);
    });
});
