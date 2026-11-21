import { PlayerOutboundSchema, type PlayerInbound, type PlayerOutbound } from "@/contract";
import { backendWsUrl, screenIdFromLocation } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { useEffect, useState } from "react";
import { CreativeView } from "./CreativeView";
import { HOUSE_AD } from "./house-ad";
import { frontCreative, initialPlayerState, playerReducer, type Playable } from "./player-machine";
import { preloadCreative } from "./preload";
import { latestAttention, toAttentionSample } from "./stages";
import { VoicePlayer } from "./voice";

const CROSSFADE_MS = 250; // matches --duration-crossfade

/** Only `listening` matters on the glass: the ADR 0005 indicator while the mic is open. */
const latestListening = (_listening: boolean, next: boolean) => next;

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function toPlayable(
    message: Extract<PlayerOutbound, { type: "creative.ready" | "creative.upgraded" }>,
): Playable {
    return {
        creativeId: message.creative_id,
        auctionId: message.auction_id,
        spec: message.spec,
        kind: message.auction_id === null ? "house" : "auction",
    };
}

export function ScreenPage() {
    const [screenId] = useState(() => screenIdFromLocation());
    const [store] = useState(() =>
        createStore(playerReducer, initialPlayerState(HOUSE_AD, Date.now())),
    );
    // Attention drives the dwell stage; it lives beside the player machine, not in it.
    const [attention] = useState(() => createStore(latestAttention, null));
    const [listening] = useState(() => createStore(latestListening, false));
    const [socket] = useState(
        () =>
            new TypedSocket<PlayerOutbound, PlayerInbound>({
                url: `${backendWsUrl}/ws/screen/${encodeURIComponent(screenId)}?attention=1&live=1`,
                schema: PlayerOutboundSchema,
                onMessage: (message) => {
                    if (message.type === "system.error") {
                        console.warn(`[screen] backend rejected a frame: ${message.code}`);
                        return;
                    }
                    if (message.screen_id !== screenId) return;
                    if (message.type === "live.status") {
                        listening.dispatch(message.listening);
                        return;
                    }
                    if (message.type === "screen.attention") {
                        if (message.screen_id === screenId) {
                            attention.dispatch(toAttentionSample(message, Date.now()));
                        }
                        return;
                    }
                    store.dispatch({
                        type: message.type === "creative.upgraded" ? "upgraded" : "received",
                        creative: toPlayable(message),
                        at: Date.now(),
                    });
                },
            }),
    );
    const [voice] = useState(() => {
        let seq = 0;
        return new VoicePlayer(undefined, undefined, (creativeId, playing, durationMs) => {
            socket.send({
                type: "player.voice",
                v: 1,
                screen_id: screenId,
                creative_id: creativeId,
                seq: ++seq,
                playing,
                duration_ms: durationMs,
            });
        });
    });
    const state = useStore(store, (s) => s);
    const micOpen = useStore(listening, (s) => s);

    useEffect(() => {
        socket.start();
        return () => {
            socket.stop();
        };
    }, [socket]);

    // Preload the back layer; a newer creative aborts the previous preload.
    const preloading = state.phase.kind === "preloading" ? state.phase : null;
    useEffect(() => {
        if (preloading === null) return;
        const controller = new AbortController();
        preloadCreative(preloading.spec, controller.signal).then(
            () => {
                if (controller.signal.aborted) return;
                store.dispatch({
                    type: "preloaded",
                    creativeId: preloading.creativeId,
                    at: Date.now(),
                });
            },
            (error: unknown) => {
                if (controller.signal.aborted) return;
                console.warn(
                    `[screen] preload failed for ${preloading.creativeId}: ${String(error)}`,
                );
                store.dispatch({
                    type: "preloadFailed",
                    creativeId: preloading.creativeId,
                    at: Date.now(),
                });
            },
        );
        return () => {
            controller.abort();
        };
    }, [preloading, store]);

    // Fetch while the back layer loads/fades. Visual readiness never awaits it.
    const back = state.layers[state.front === "a" ? "b" : "a"];
    const nextVoice = state.phase.kind === "showing" ? null : (back?.spec.voiceover_url ?? null);
    useEffect(() => {
        if (nextVoice === null) return;
        const controller = new AbortController();
        void voice.prepare(nextVoice, controller.signal).catch(() => {});
        return () => {
            controller.abort();
        };
    }, [nextVoice, voice]);

    // Prepare voice independently of visual readiness; late audio may still play
    // while its creative is on air. A replacement cancels the old line.
    const onAir = frontCreative(state);
    const onAirVoice = onAir.spec.voiceover_url;
    useEffect(() => {
        voice.stop();
        if (onAirVoice === null) return;
        const controller = new AbortController();
        const play = () => {
            if (!controller.signal.aborted) voice.play(onAir.creativeId, onAirVoice);
        };
        if (voice.isReady(onAirVoice)) play();
        else {
            void voice.prepare(onAirVoice, controller.signal).then(play, () => {
                // Optional: failed audio leaves the visible creative on air.
            });
        }
        return () => {
            controller.abort();
            voice.stop();
        };
    }, [onAir.creativeId, onAirVoice, voice]);

    // Finish the crossfade on a timer (transitionend doesn't fire at 0 ms).
    const crossfadingId = state.phase.kind === "crossfading" ? state.phase.creativeId : null;
    useEffect(() => {
        if (crossfadingId === null) return;
        const timer = setTimeout(
            () => {
                store.dispatch({
                    type: "crossfadeDone",
                    creativeId: crossfadingId,
                    at: Date.now(),
                });
            },
            reducedMotion() ? 0 : CROSSFADE_MS,
        );
        return () => {
            clearTimeout(timer);
        };
    }, [crossfadingId, store]);

    // Report each finished play window; the backend computes attention-seconds.
    const completed = state.completed;
    useEffect(() => {
        if (completed === null) return;
        socket.send({
            type: "playback.report",
            v: 1,
            screen_id: screenId,
            creative_id: completed.creativeId,
            seq: completed.seq,
            started_at: new Date(completed.startedAt).toISOString(),
            ended_at: new Date(completed.endedAt).toISOString(),
        });
    }, [completed, screenId, socket]);

    // Close glass-to-glass: once per creative made visible (never on an in-place upgrade).
    const displayed = state.displayed;
    useEffect(() => {
        socket.send({
            type: "player.displayed",
            v: 1,
            screen_id: screenId,
            creative_id: displayed.creativeId,
            seq: displayed.seq,
            displayed_at: new Date(displayed.displayedAt).toISOString(),
        });
    }, [displayed, screenId, socket]);

    return (
        <main className="relative h-screen w-screen cursor-none overflow-hidden bg-canvas">
            {(["a", "b"] as const).map((layer) => {
                const creative = state.layers[layer];
                if (creative === null) return null;
                const isFront = layer === state.front;
                const fadingIn = !isFront && state.phase.kind === "crossfading";
                return (
                    <div
                        key={layer}
                        className="absolute inset-0"
                        style={{
                            opacity: isFront || fadingIn ? 1 : 0,
                            // Runs from 0 on every swap, however fast the preload was.
                            animation: fadingIn
                                ? "player-crossfade-in var(--duration-crossfade) ease-out both"
                                : undefined,
                            zIndex: isFront ? "var(--z-back-layer)" : "var(--z-front-layer)",
                        }}
                        aria-hidden={!isFront}
                        // Test hook: which creative this layer holds (evals/browser.py).
                        data-creative-id={creative.creativeId}
                    >
                        <CreativeView
                            spec={creative.spec}
                            playKey={creative.creativeId}
                            readAttention={attention.getState}
                        />
                    </div>
                );
            })}
            {micOpen && (
                <div
                    className="absolute bottom-[3vh] left-[3vh] flex items-center gap-2 rounded-full bg-ink px-4 py-2 text-compact font-medium text-white shadow-overlay"
                    style={{ zIndex: "var(--z-overlay)" }}
                    role="status"
                    data-listening="on"
                >
                    <span className="size-2 rounded-full bg-danger" aria-hidden />
                    Micrófono activo · solo temas, nada se graba
                </div>
            )}
        </main>
    );
}
