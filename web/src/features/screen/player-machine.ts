import type { CreativeSpec } from "@/contract";

/**
 * The player as a pure state machine (docs/stack.md §6): two stacked layers
 * (A/B double buffer). A new creative mounts hidden in the back layer, is
 * preloaded (required logos decoded), then crossfades over the
 * front. The front layer always holds something: the house ad or the last good
 * creative. The screen is never blank.
 *
 * Latest wins: a creative that arrives while another is preloading replaces
 * it; one that arrives mid-crossfade waits in `queued` (only the newest).
 * Every event carries `at` (epoch ms) so the machine is deterministic.
 */

export type LayerId = "a" | "b";

export interface Playable {
    readonly creativeId: string;
    readonly auctionId: string | null;
    readonly spec: CreativeSpec;
    readonly kind: "house" | "auction";
}

export type Phase =
    | { readonly kind: "showing" }
    | { readonly kind: "preloading"; readonly creativeId: string; readonly spec: CreativeSpec }
    | { readonly kind: "crossfading"; readonly creativeId: string };

/** A creative that left the screen; reported to the backend as playback.report. */
export interface CompletedPlay {
    readonly seq: number;
    readonly creativeId: string;
    readonly startedAt: number;
    readonly endedAt: number;
}

/** A creative that just became visible; reported as player.displayed (glass-to-glass). */
export interface DisplayedPlay {
    readonly seq: number;
    readonly creativeId: string;
    readonly displayedAt: number;
}

export interface PlayerState {
    readonly front: LayerId;
    readonly layers: Readonly<Record<LayerId, Playable | null>>;
    readonly phase: Phase;
    readonly queued: Playable | null;
    readonly shownSince: number;
    readonly completed: CompletedPlay | null;
    /** The latest creative made visible; changes once per new creative, never on an upgrade. */
    readonly displayed: DisplayedPlay;
}

export type PlayerEvent =
    | { readonly type: "received" | "upgraded"; readonly creative: Playable; readonly at: number }
    | { readonly type: "preloaded"; readonly creativeId: string; readonly at: number }
    | { readonly type: "preloadFailed"; readonly creativeId: string; readonly at: number }
    | { readonly type: "crossfadeDone"; readonly creativeId: string; readonly at: number };

export const other = (layer: LayerId): LayerId => (layer === "a" ? "b" : "a");

export function initialPlayerState(houseAd: Playable, at: number): PlayerState {
    return {
        front: "a",
        layers: { a: houseAd, b: null },
        phase: { kind: "showing" },
        queued: null,
        shownSince: at,
        completed: null,
        displayed: { seq: 1, creativeId: houseAd.creativeId, displayedAt: at },
    };
}

export function frontCreative(state: PlayerState): Playable {
    const creative = state.layers[state.front];
    if (creative === null) throw new Error("invariant: the front layer is never empty");
    return creative;
}

function loadIntoBack(state: PlayerState, creative: Playable): PlayerState {
    return {
        ...state,
        layers: { ...state.layers, [other(state.front)]: creative },
        phase: { kind: "preloading", creativeId: creative.creativeId, spec: creative.spec },
    };
}

/** Reuse a known creative only while its auction and required brand assets match. */
function updateKnownCreative(state: PlayerState, creative: Playable): PlayerState | null {
    const compatible = (previous: Playable) =>
        previous.auctionId === creative.auctionId &&
        previous.kind === creative.kind &&
        previous.spec.slots.length === creative.spec.slots.length &&
        previous.spec.slots.every((slot, index) => {
            const incoming = creative.spec.slots[index];
            return (
                incoming !== undefined &&
                slot.brand_id === incoming.brand_id &&
                slot.logo_url === incoming.logo_url
            );
        });
    if (state.queued?.creativeId === creative.creativeId) {
        return compatible(state.queued) ? { ...state, queued: creative } : null;
    }
    for (const layer of [other(state.front), state.front]) {
        const previous = state.layers[layer];
        if (previous?.creativeId !== creative.creativeId) continue;
        if (!compatible(previous)) return null;
        return { ...state, layers: { ...state.layers, [layer]: creative } };
    }
    return null;
}

export function playerReducer(state: PlayerState, event: PlayerEvent): PlayerState {
    switch (event.type) {
        case "upgraded":
            // An upgrade cannot introduce a new play or revive a retired creative.
            return updateKnownCreative(state, event.creative) ?? state;
        case "received": {
            const updated = updateKnownCreative(state, event.creative);
            if (updated !== null) {
                // A newer ready for the ad on air beats the one still preloading: latest wins.
                const resentOnAir =
                    state.layers[state.front]?.creativeId === event.creative.creativeId &&
                    state.phase.kind === "preloading" &&
                    state.phase.creativeId !== event.creative.creativeId;
                return resentOnAir
                    ? {
                          ...updated,
                          layers: { ...updated.layers, [other(state.front)]: null },
                          phase: { kind: "showing" },
                      }
                    : updated;
            }
            if (state.phase.kind === "crossfading") return { ...state, queued: event.creative };
            return loadIntoBack(state, event.creative);
        }
        case "preloaded": {
            if (state.phase.kind !== "preloading" || state.phase.creativeId !== event.creativeId) {
                return state; // stale: a newer creative replaced it
            }
            return { ...state, phase: { kind: "crossfading", creativeId: event.creativeId } };
        }
        case "preloadFailed": {
            if (state.phase.kind !== "preloading" || state.phase.creativeId !== event.creativeId) {
                return state;
            }
            // Keep showing what we have; never swap to something that can't render.
            const cleared: PlayerState = {
                ...state,
                layers: { ...state.layers, [other(state.front)]: null },
                phase: { kind: "showing" },
            };
            return state.queued
                ? loadIntoBack({ ...cleared, queued: null }, state.queued)
                : cleared;
        }
        case "crossfadeDone": {
            if (state.phase.kind !== "crossfading" || state.phase.creativeId !== event.creativeId) {
                return state;
            }
            const previous = frontCreative(state);
            const sameCreative = previous.creativeId === event.creativeId; // an in-place upgrade
            const completed: CompletedPlay | null =
                sameCreative || previous.kind === "house"
                    ? state.completed
                    : {
                          seq: (state.completed?.seq ?? 0) + 1,
                          creativeId: previous.creativeId,
                          startedAt: state.shownSince,
                          endedAt: event.at,
                      };
            const swapped: PlayerState = {
                ...state,
                front: other(state.front),
                layers: { ...state.layers, [state.front]: null },
                phase: { kind: "showing" },
                queued: null,
                shownSince: sameCreative ? state.shownSince : event.at,
                completed,
                displayed: sameCreative
                    ? state.displayed
                    : {
                          seq: state.displayed.seq + 1,
                          creativeId: event.creativeId,
                          displayedAt: event.at,
                      },
            };
            return state.queued ? loadIntoBack(swapped, state.queued) : swapped;
        }
    }
}
