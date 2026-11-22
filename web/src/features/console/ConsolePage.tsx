import { ConsoleOutboundSchema, type ConsoleOutbound } from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { HEARTBEAT_IDLE_MS } from "@/lib/ws/heartbeat";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { AppShell } from "@/shared/ui/page";
import { useEffect, useState } from "react";
import { consoleReducer, emptyScreen, initialConsoleState } from "./console-state";
import { DEGRADATION } from "./labels";
import { AuctionRace, Meters } from "./live";
import { CameraPanel, PeopleScreen } from "./station-d";
import { StationBar } from "./StationBar";

const SCREEN_ID = /^[A-Za-z0-9_.:-]{1,64}$/;

/**
 * The station's home: what the camera sees and why, what people see and what is
 * next, and the auction. One socket: the console stream, which also
 * carries the camera's anonymous detection boxes (`dev.perception`, no image).
 */
export function ConsolePage() {
    const [store] = useState(() => createStore(consoleReducer, initialConsoleState));
    const [socket] = useState(
        () =>
            new TypedSocket<ConsoleOutbound>({
                // ?live=1: a station console also gets what airs, what is next and the book.
                url: `${backendWsUrl}/ws/console?live=1`,
                // A dead Wi-Fi link must read "reconnecting", not "En vivo" with frozen numbers.
                idleTimeoutMs: HEARTBEAT_IDLE_MS,
                schema: ConsoleOutboundSchema,
                onMessage: (message) => {
                    const at = Date.now();
                    store.dispatch(
                        message.type === "dev.perception"
                            ? { type: "perception", message, at }
                            : { type: "message", message, at },
                    );
                },
                onStateChange: (state) => {
                    store.dispatch({ type: "connection", state });
                },
            }),
    );
    useEffect(() => {
        socket.start();
        return () => {
            socket.stop();
        };
    }, [socket]);

    const connection = useStore(store, (s) => s.connection);
    const screens = useStore(store, (s) => s.screens);
    const lastError = useStore(store, (s) => s.lastError);
    const requested = new URLSearchParams(window.location.search).get("screen");
    const screen = requested && SCREEN_ID.test(requested) ? requested : "demo-01";
    const view = screens[screen] ?? emptyScreen;
    // Missing image tags only lose an upgrade; the hot path still decides, so the console stays quiet about it.
    const shownDegradation = view.degradation.filter((flag) => flag !== "vlm_unavailable");

    return (
        <AppShell>
            <ErrorBoundary label="station">
                <StationBar screen={screen} connection={connection} />
            </ErrorBoundary>
            {(lastError || shownDegradation.length > 0) && (
                <div className="flex flex-wrap gap-2">
                    {lastError && (
                        <Badge tone="danger" appearance="soft" title={lastError}>
                            Error del servidor
                        </Badge>
                    )}
                    {shownDegradation.map((flag) => (
                        <Badge key={flag} tone="warning" appearance="soft">
                            {DEGRADATION[flag]}
                        </Badge>
                    ))}
                </div>
            )}
            {/* The live loop, left to right: what the camera sees, who wins, what airs. */}
            <section
                aria-label="Ciclo en vivo"
                className="grid divide-y divide-border border-y border-border lg:grid-cols-[1.25fr_1fr_1.1fr] lg:divide-x lg:divide-y-0"
            >
                <ErrorBoundary label="camera">
                    <CameraPanel view={view} />
                </ErrorBoundary>
                <ErrorBoundary label="race">
                    <AuctionRace view={view} />
                </ErrorBoundary>
                <ErrorBoundary label="people">
                    <PeopleScreen view={view} />
                </ErrorBoundary>
            </section>
            <ErrorBoundary label="meters">
                <Meters view={view} />
            </ErrorBoundary>
        </AppShell>
    );
}
