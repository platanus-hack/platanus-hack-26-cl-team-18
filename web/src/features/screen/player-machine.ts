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
    | { readonly kind: "preloading"; readonly creativeId: string }
    | { readonly kind: "crossfading"; readonly creativeId: string };

/** A creative that left the screen; reported to the backend as playback.report. */
export interface CompletedPlay {
    readonly seq: number;
    readonly creativeId: string;
    readonly startedAt: number;
    readonly endedAt: number;
}

export interface PlayerState {
    readonly front: LayerId;
    readonly layers: Readonly<Record<LayerId, Playable | null>>;
    readonly phase: Phase;
    readonly queued: Playable | null;
    readonly shownSince: number;
    readonly completed: CompletedPlay | null;
}

export type PlayerEvent =
    | { readonly type: "received"; readonly creative: Playable; readonly at: number }
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
        phase: { kind: "preloading", creativeId: creative.creativeId },
    };
}

export function playerReducer(state: PlayerState, event: PlayerEvent): PlayerState {
    switch (event.type) {
        case "received": {
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
            };
            return state.queued ? loadIntoBack(swapped, state.queued) : swapped;
        }
    }
}
