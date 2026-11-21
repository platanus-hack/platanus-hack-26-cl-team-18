import { describe, expect, it } from "vitest";
import { HOUSE_AD } from "./house-ad";
import type { Playable } from "./player-machine";
import { frontCreative, initialPlayerState, playerReducer } from "./player-machine";

/** Latest wins, including when the server's latest intent is the ad already on air. */

const ad = (id: string, copy = id): Playable => ({
    creativeId: id,
    auctionId: `au-${id}`,
    kind: "auction",
    spec: { ...HOUSE_AD.spec, slots: [{ ...HOUSE_AD.spec.slots[0]!, copy_text: copy }] },
});

function onAir(id: string) {
    let s = initialPlayerState(HOUSE_AD, 0);
    s = playerReducer(s, { type: "received", creative: ad(id), at: 1 });
    s = playerReducer(s, { type: "preloaded", creativeId: id, at: 2 });
    return playerReducer(s, { type: "crossfadeDone", creativeId: id, at: 252 });
}

describe("the on-air creative is re-sent while another preloads", () => {
    it("keeps the on-air creative and discards the stale preload", () => {
        let s = onAir("x");
        s = playerReducer(s, { type: "received", creative: ad("y"), at: 300 });
        expect(s.phase).toMatchObject({ kind: "preloading", creativeId: "y" });
        s = playerReducer(s, { type: "received", creative: ad("x", "x again"), at: 310 });
        expect(s.phase).toEqual({ kind: "showing" });
        expect(frontCreative(s).spec.slots[0]?.copy_text).toBe("x again");
        expect(Object.values(s.layers).filter(Boolean)).toHaveLength(1);
        // The discarded preload finishing late changes nothing.
        expect(playerReducer(s, { type: "preloaded", creativeId: "y", at: 400 })).toBe(s);
        expect(s.displayed.creativeId).toBe("x"); // no new play, no new display
    });

    it("a warm upgrade of the on-air creative does not cancel the newer preload", () => {
        let s = onAir("x");
        s = playerReducer(s, { type: "received", creative: ad("y"), at: 300 });
        s = playerReducer(s, { type: "upgraded", creative: ad("x", "x upgraded"), at: 310 });
        expect(s.phase).toMatchObject({ kind: "preloading", creativeId: "y" });
    });
});
