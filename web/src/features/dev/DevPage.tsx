import { DevOutboundSchema, type DevOutbound } from "@/contract";
import {
    AuctionPanel,
    AudiencePanel,
    CONNECTION,
    DEGRADATION,
    LatencyPanel,
    type ScreenView,
} from "@/features/console";
import { backendWsUrl } from "@/lib/config";
import { demoKey } from "@/lib/demo";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { Card } from "@/shared/ui/card";
import { AppShell, EmptyState, PageHeader } from "@/shared/ui/page";
import { Bug } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
    buildTimeline,
    devReducer,
    emptyDevScreen,
    initialDevState,
    jobSpans,
    providerLoad,
} from "./dev-state";
import { EventLog } from "./event-log";
import { useNow } from "./format";
import { AuctionHistory, OnAirPanel } from "./on-air";
import { PerceptionPanel, SpeechPanel, VlmPanel } from "./perception";
import { TimelinePanel } from "./timeline";

/** The backend closes /ws/dev with this code where dev views are off (prod). */
export const DEV_DISABLED_CODE = 4404;
const TIMELINE_WINDOW_MS = 20_000;
// dev.keyframe carries a base64 JPEG; the default 1 MB cap would drop it.
const MAX_FRAME_BYTES = 3_000_000;

const emptyConsoleScreen: ScreenView = {
    moment: null,
    auction: null,
    lastTrace: null,
    glassToGlassMs: [],
    degradation: [],
    lastCreativeId: null,
    book: null,
    receipts: [],
    redemptions: [],
};

export function screenFromLocation(search: string): string {
    const id = new URLSearchParams(search).get("screen") ?? "demo-01";
    return /^[A-Za-z0-9_.:-]{1,64}$/.test(id) ? id : "demo-01";
}

/**
 * /dev: QA and developer view of the whole loop for one screen. Read-only: it
 * mirrors the ad from the dev socket and never connects as a player.
 */
export function DevPage() {
    const [store] = useState(() => createStore(devReducer, initialDevState));
    const [socket] = useState(() => {
        const s: TypedSocket<DevOutbound> = new TypedSocket<DevOutbound>({
            // Demo surface: a socket can't send headers, so the key rides as ?key=.
            url: `${backendWsUrl}/ws/dev?key=${encodeURIComponent(demoKey() ?? "")}`,
            schema: DevOutboundSchema,
            maxFrameBytes: MAX_FRAME_BYTES,
            onMessage: (message) => {
                store.dispatch({ type: "message", message, receivedAtMs: Date.now() });
            },
            onStateChange: (state) => {
                store.dispatch({ type: "connection", state });
            },
            onClose: (code) => {
                if (code === DEV_DISABLED_CODE) {
                    store.dispatch({ type: "disabled" });
                    s.stop();
                }
            },
        });
        return s;
    });
    useEffect(() => {
        socket.start();
        return () => {
            socket.stop();
        };
    }, [socket]);

    const [screen, setScreen] = useState(() => screenFromLocation(window.location.search));
    const disabled = useStore(store, (s) => s.disabled);
    const connection = useStore(store, (s) => s.console.connection);
    const lastError = useStore(store, (s) => s.console.lastError);
    const consoleScreens = useStore(store, (s) => s.console.screens);
    const devScreens = useStore(store, (s) => s.screens);
    const jobs = useStore(store, (s) => s.jobs);
    const log = useStore(store, (s) => s.log);
    const paused = useStore(store, (s) => s.paused);
    const nowMs = useNow(1000);

    const view = devScreens[screen] ?? emptyDevScreen;
    const consoleView = consoleScreens[screen] ?? emptyConsoleScreen;
    const known = [...new Set([screen, ...Object.keys(devScreens)])].sort();
    const load = providerLoad(jobs);
    const timeline = useMemo(
        () =>
            buildTimeline(
                view.traces,
                jobSpans(jobs).filter((j) => j.screenId === null || j.screenId === screen),
                { windowMs: TIMELINE_WINDOW_MS, nowMs },
            ),
        [view.traces, jobs, screen, nowMs],
    );
    const { tone, label } = CONNECTION[connection.status];

    if (disabled) {
        return (
            <AppShell>
                <PageHeader title="Dev / QA" />
                <Card>
                    <EmptyState
                        icon={<Bug />}
                        title="Consola dev desactivada en este entorno"
                        description="El backend cerró /ws/dev (4404): falta la clave de demo o es producción."
                    />
                </Card>
            </AppShell>
        );
    }

    return (
        <AppShell>
            <PageHeader
                title="Dev / QA"
                description="Todo el ciclo de una pantalla en vivo: percepción, VLM, voz, subasta, creatividad y lo que está al aire."
                actions={
                    <>
                        <label className="flex items-center gap-2 text-compact text-muted">
                            Pantalla
                            <select
                                className="rounded-control border border-border-strong bg-surface px-2 py-1 text-fg"
                                value={screen}
                                onChange={(e) => {
                                    const next = e.target.value;
                                    setScreen(next);
                                    const url = new URL(window.location.href);
                                    url.searchParams.set("screen", next);
                                    window.history.replaceState(null, "", url);
                                }}
                            >
                                {known.map((id) => (
                                    <option key={id} value={id}>
                                        {id}
                                    </option>
                                ))}
                            </select>
                        </label>
                        {lastError && (
                            <Badge tone="danger" appearance="soft" title={lastError}>
                                Error del servidor
                            </Badge>
                        )}
                        <Badge tone={tone} appearance="soft">
                            {label}
                        </Badge>
                    </>
                }
            />
            <div className="flex flex-wrap items-center gap-2" aria-label="Estado">
                {consoleView.degradation.map((flag) => (
                    <Badge key={flag} tone="warning" appearance="soft">
                        {DEGRADATION[flag]}
                    </Badge>
                ))}
                {load.length === 0 ? (
                    <Badge appearance="soft">Sin trabajos tibios</Badge>
                ) : (
                    load.map((l) => (
                        <Badge
                            key={l.provider}
                            tone={l.in_flight > 0 ? "info" : "neutral"}
                            appearance="soft"
                        >
                            {l.provider}: {l.in_flight} en curso
                        </Badge>
                    ))
                )}
            </div>
            <div className="grid gap-4 xl:grid-cols-3">
                <div className="flex min-w-0 flex-col gap-4">
                    <PerceptionPanel view={view} nowMs={nowMs} />
                    <VlmPanel view={view} />
                    <SpeechPanel view={view} nowMs={nowMs} />
                </div>
                <div className="flex min-w-0 flex-col gap-4">
                    <AudiencePanel view={consoleView} />
                    <AuctionPanel view={consoleView} />
                    <LatencyPanel view={consoleView} />
                    <AuctionHistory auctions={view.auctions} />
                </div>
                <div className="flex min-w-0 flex-col gap-4">
                    <OnAirPanel view={view} />
                </div>
            </div>
            <TimelinePanel timeline={timeline} windowMs={TIMELINE_WINDOW_MS} />
            <EventLog
                log={log}
                screen={screen}
                paused={paused}
                onPause={(next) => {
                    store.dispatch({ type: "pause", paused: next });
                }}
            />
        </AppShell>
    );
}
