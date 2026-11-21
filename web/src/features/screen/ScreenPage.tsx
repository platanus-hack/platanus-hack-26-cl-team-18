import { PlayerOutboundSchema, type PlaybackReport, type PlayerOutbound } from "@/contract";
import { backendWsUrl, screenIdFromLocation } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { useEffect, useState } from "react";
import { CreativeView } from "./CreativeView";
import { HOUSE_AD } from "./house-ad";
import {
    frontCreative,
    initialPlayerState,
    other,
    playerReducer,
    type Playable,
} from "./player-machine";
import { preloadCreative } from "./preload";
import { VoicePlayer } from "./voice";

const CROSSFADE_MS = 250; // matches --duration-crossfade

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
    const [socket] = useState(
        () =>
            new TypedSocket<PlayerOutbound, PlaybackReport>({
                url: `${backendWsUrl}/ws/screen/${encodeURIComponent(screenId)}`,
                schema: PlayerOutboundSchema,
                onMessage: (message) => {
                    if (message.type === "system.error") {
                        console.warn(`[screen] backend rejected a frame: ${message.code}`);
                        return;
                    }
                    store.dispatch({
                        type: "received",
                        creative: toPlayable(message),
                        at: Date.now(),
                    });
                },
            }),
    );
    const [voice] = useState(() => new VoicePlayer());
    const state = useStore(store, (s) => s);

    useEffect(() => {
        socket.start();
        return () => {
            socket.stop();
        };
    }, [socket]);

    // Preload the back layer; a newer creative aborts the previous preload.
    const preloadingId = state.phase.kind === "preloading" ? state.phase.creativeId : null;
    const back = state.layers[other(state.front)];
    useEffect(() => {
        if (preloadingId === null || back === null) return;
        const controller = new AbortController();
        preloadCreative(back.spec, controller.signal).then(
            () => {
                store.dispatch({ type: "preloaded", creativeId: preloadingId, at: Date.now() });
            },
            (error: unknown) => {
                if (controller.signal.aborted) return;
                console.warn(`[screen] preload failed for ${preloadingId}: ${String(error)}`);
                store.dispatch({ type: "preloadFailed", creativeId: preloadingId, at: Date.now() });
            },
        );
        return () => {
            controller.abort();
        };
    }, [preloadingId, back, store]);

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
                        className="absolute inset-0 transition-opacity ease-out"
                        style={{
                            transitionDuration: "var(--duration-crossfade)",
                            opacity: isFront || fadingIn ? 1 : 0,
                            zIndex: isFront ? "var(--z-back-layer)" : "var(--z-front-layer)",
                        }}
                        aria-hidden={!isFront}
                    >
                        <CreativeView spec={creative.spec} playKey={creative.creativeId} />
                    </div>
                );
            })}
        </main>
    );
}
