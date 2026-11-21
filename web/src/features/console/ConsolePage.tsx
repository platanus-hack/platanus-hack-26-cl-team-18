import { ConsoleOutboundSchema, type ConsoleOutbound } from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { AppShell } from "@/shared/ui/page";
import { useEffect, useState } from "react";
import { consoleReducer, emptyScreen, initialConsoleState } from "./console-state";
import { DEGRADATION } from "./labels";
import { AudiencePanel, LatencyPanel } from "./panels";
import { BidsPanel, OnAirPanel, SessionPanel, Timeline } from "./station-panels";
import { StationBar } from "./StationBar";

const SCREEN_ID = /^[A-Za-z0-9_.:-]{1,64}$/;
/** The station's home: control bar, what airs, who bids and why, what it earned. */
export function ConsolePage() {
    const [store] = useState(() => createStore(consoleReducer, initialConsoleState));
    const [socket] = useState(
        () =>
            new TypedSocket<ConsoleOutbound>({
                // ?live=1: a station console also gets what airs and the book in standby.
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

    return (
        <AppShell>
            <StationBar screen={screen} screens={Object.keys(screens)} connection={connection} />
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
            <div className="grid gap-4 lg:grid-cols-[1fr_1.5fr_1fr]">
                <div className="flex flex-col gap-4">
                    <OnAirPanel view={view} />
                    <AudiencePanel view={view} />
                </div>
                <BidsPanel view={view} screen={screen} />
                <SessionPanel view={view} />
            </div>
            <div className="grid gap-4 lg:grid-cols-[1fr_2.5fr]">
                <LatencyPanel view={view} />
                <Timeline view={view} />
            </div>
        </AppShell>
    );
}
