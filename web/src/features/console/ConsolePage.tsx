import {
    ConsoleOutboundSchema,
    DevOutboundSchema,
    type ConsoleOutbound,
    type DevOutbound,
} from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { demoKey, rememberedDemoKey } from "@/lib/demo";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";
import { AppShell } from "@/shared/ui/page";
import { useEffect, useState } from "react";
import { consoleReducer, emptyScreen, initialConsoleState } from "./console-state";
import { DEGRADATION } from "./labels";
import { AuctionSection, CameraPanel, PeopleScreen, TagsPanel, WhyPanel } from "./station-d";
import { SessionPanel, Timeline } from "./station-panels";
import { StationBar } from "./StationBar";

const SCREEN_ID = /^[A-Za-z0-9_.:-]{1,64}$/;
// dev.keyframe frames can be large; this socket only reads the detection boxes.
const DEV_MAX_FRAME_BYTES = 3_000_000;

/**
 * The station's home: what the camera sees and why, what people see and what is
 * next, the auction, then the session. Two sockets: the console stream, and (with
 * the demo key) the dev stream for the camera's detection boxes.
 */
export function ConsolePage() {
    const [store] = useState(() => createStore(consoleReducer, initialConsoleState));
    const [socket] = useState(
        () =>
            new TypedSocket<ConsoleOutbound>({
                // ?live=1: a station console also gets what airs, what is next and the book.
                url: `${backendWsUrl}/ws/console?live=1`,
                schema: ConsoleOutboundSchema,
                onMessage: (message) => {
                    store.dispatch({ type: "message", message, at: Date.now() });
                },
                onStateChange: (state) => {
                    store.dispatch({ type: "connection", state });
                },
            }),
    );
    const [camera] = useState(() => {
        const key = demoKey() ?? rememberedDemoKey();
        if (key === null) return null;
        return new TypedSocket<DevOutbound>({
            url: `${backendWsUrl}/ws/dev?key=${encodeURIComponent(key)}`,
            schema: DevOutboundSchema,
            maxFrameBytes: DEV_MAX_FRAME_BYTES,
            onMessage: (message) => {
                if (message.type === "dev.perception") {
                    store.dispatch({ type: "perception", message, at: Date.now() });
                }
            },
        });
    });
    useEffect(() => {
        socket.start();
        camera?.start();
        return () => {
            socket.stop();
            camera?.stop();
        };
    }, [socket, camera]);

    const connection = useStore(store, (s) => s.connection);
    const screens = useStore(store, (s) => s.screens);
    const lastError = useStore(store, (s) => s.lastError);
    const requested = new URLSearchParams(window.location.search).get("screen");
    const screen = requested && SCREEN_ID.test(requested) ? requested : "demo-01";
    const view = screens[screen] ?? emptyScreen;

    return (
        <AppShell>
            <ErrorBoundary label="station">
                <StationBar
                    screen={screen}
                    screens={Object.keys(screens)}
                    connection={connection}
                />
            </ErrorBoundary>
            {(lastError || view.degradation.length > 0) && (
                <div className="flex flex-wrap gap-2">
                    {lastError && (
                        <Badge tone="danger" appearance="soft" title={lastError}>
                            Error del servidor
                        </Badge>
                    )}
                    {view.degradation.map((flag) => (
                        <Badge key={flag} tone="warning" appearance="soft">
                            {DEGRADATION[flag]}
                        </Badge>
                    ))}
                </div>
            )}
            <div className="grid gap-7 lg:grid-cols-2">
                <ErrorBoundary label="camera">
                    <CameraPanel view={view} />
                </ErrorBoundary>
                <ErrorBoundary label="people">
                    <PeopleScreen view={view} />
                </ErrorBoundary>
            </div>
            <ErrorBoundary label="auction">
                <AuctionSection view={view} screen={screen} />
            </ErrorBoundary>
            <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
                <ErrorBoundary label="tags">
                    <TagsPanel view={view} />
                </ErrorBoundary>
                <ErrorBoundary label="why">
                    <WhyPanel view={view} />
                </ErrorBoundary>
            </div>
            <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
                <ErrorBoundary label="session">
                    <SessionPanel view={view} />
                </ErrorBoundary>
                <ErrorBoundary label="timeline">
                    <Timeline view={view} />
                </ErrorBoundary>
            </div>
        </AppShell>
    );
}
