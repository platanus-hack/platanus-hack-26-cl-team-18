import { ConsoleOutboundSchema, type ConsoleOutbound, type ExclusionReason } from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket, type ConnectionState } from "@/lib/ws/typed-socket";
import { Empty, Panel } from "@/shared/ui/Panel";
import { StatusBadge, type Tone } from "@/shared/ui/StatusBadge";
import { useEffect, useState } from "react";
import { consoleReducer, initialConsoleState, type ScreenView } from "./console-state";
import { coBrandLift, percentile, serverDecisionMs, traceSteps } from "./metrics";

const CONNECTION: Record<ConnectionState["status"], { tone: Tone; label: string }> = {
    open: { tone: "success", label: "Live" },
    connecting: { tone: "info", label: "Connecting" },
    reconnecting: { tone: "warning", label: "Reconnecting" },
    closed: { tone: "danger", label: "Offline" },
};

const EXCLUSION_LABEL: Record<ExclusionReason, string> = {
    competitor: "Competitor",
    safety: "Minor safety",
    budget: "Budget",
    below_reserve: "Below reserve",
    no_layout: "No layout",
    not_partner: "Not a partner",
    out_of_stock: "Out of stock",
    inactive: "Inactive",
    no_basket_offer: "No basket offer",
    outbid: "Outbid",
    invalid_bid: "Invalid bid",
};

const fmtMs = (value: number | null) =>
    value === null ? "—" : value === 0 ? "<1 ms" : `${Math.round(value)} ms`;

export function ConsolePage() {
    const [store] = useState(() => createStore(consoleReducer, initialConsoleState));
    const [socket] = useState(
        () =>
            new TypedSocket<ConsoleOutbound>({
                url: `${backendWsUrl}/ws/console`,
                schema: ConsoleOutboundSchema,
                onMessage: (message) => {
                    store.dispatch({ type: "message", message });
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
    const { tone, label } = CONNECTION[connection.status];

    return (
        <div className="flex min-h-screen flex-col gap-4 p-6">
            <header className="flex items-center justify-between">
                <h1 className="text-section font-semibold tracking-tight">Console</h1>
                <div className="flex items-center gap-3">
                    {lastError && <StatusBadge tone="danger" label={lastError} />}
                    <StatusBadge tone={tone} label={label} />
                </div>
            </header>
            {Object.keys(screens).length === 0 ? (
                <Panel title="Waiting for screens">
                    <Empty>No screen activity yet. Start the agent or the simulator.</Empty>
                </Panel>
            ) : (
                Object.entries(screens).map(([screenId, view]) => (
                    <ScreenRow key={screenId} screenId={screenId} view={view} />
                ))
            )}
        </div>
    );
}

function ScreenRow({ screenId, view }: { screenId: string; view: ScreenView }) {
    return (
        <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr_1fr]">
            <MomentPanel screenId={screenId} view={view} />
            <AuctionPanel view={view} />
            <LatencyPanel view={view} />
            {view.degradation.length > 0 && (
                <div className="flex flex-wrap gap-2 lg:col-span-3">
                    {view.degradation.map((flag) => (
                        <StatusBadge key={flag} tone="warning" label={flag.replaceAll("_", " ")} />
                    ))}
                </div>
            )}
        </div>
    );
}

function MomentPanel({ screenId, view }: { screenId: string; view: ScreenView }) {
    const m = view.moment;
    return (
        <Panel
            title={`Audience · ${screenId}`}
            aside={
                m && (
                    <span className="text-metadata text-muted tabular-nums">
                        {m.people} people · {m.looking} looking
                    </span>
                )
            }
        >
            {!m ? (
                <Empty>No moment yet.</Empty>
            ) : (
                <ul className="flex flex-col gap-2">
                    {m.minor_likely.value && (
                        <li>
                            <StatusBadge
                                tone="warning"
                                label="Minor likely: restricted categories blocked"
                            />
                        </li>
                    )}
                    {m.segments.length === 0 && (
                        <li className="text-muted">No matching segments</li>
                    )}
                    {m.segments.map((s) => (
                        <li key={s.segment_id} className="flex flex-col gap-1">
                            <div className="flex justify-between gap-2">
                                <span>{s.label}</span>
                                <span className="text-metadata text-muted tabular-nums">
                                    {Math.round(s.score * 100)}% · {s.sources.join(", ")}
                                </span>
                            </div>
                            <div className="h-1.5 rounded-control bg-surface-raised">
                                <div
                                    className="h-full rounded-control bg-accent"
                                    style={{ width: `${Math.round(s.score * 100)}%` }}
                                />
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </Panel>
    );
}

function AuctionPanel({ view }: { view: ScreenView }) {
    const a = view.auction?.moment_id === view.moment?.moment_id ? view.auction : null;
    const lift = a ? coBrandLift(a.bundle_value.amount, a.single_brand_value.amount) : null;
    // Like-for-like revenue lift (R-O3); shown only when it is real (D6).
    const revenueLift =
        a?.bundle_revenue && a.single_brand_revenue
            ? coBrandLift(a.bundle_revenue.amount, a.single_brand_revenue.amount)
            : null;
    const holdUntil = a?.swap === "held" ? a.trace.swap_not_before : null;
    return (
        <Panel
            title="Bundle auction"
            aside={
                a &&
                Boolean(view.moment?.segments.length) && (
                    <span className="text-metadata text-muted tabular-nums">
                        Viewer coverage (est.) {Math.round(a.coverage * 100)}%
                    </span>
                )
            }
        >
            {!a ? (
                <Empty>No auction yet.</Empty>
            ) : (
                <div className="flex flex-col gap-3">
                    {holdUntil && (
                        <StatusBadge
                            tone="neutral"
                            label={`Swapping at ${new Date(holdUntil).toLocaleTimeString()} (min ad time)`}
                        />
                    )}
                    {a.winners.length === 0 ? (
                        <StatusBadge tone="neutral" label="No fill: house ad" />
                    ) : (
                        a.winners.map((w) => (
                            <div key={w.campaign_id} className="flex justify-between">
                                <span className="font-medium">{w.brand_name}</span>
                                <span className="tabular-nums">
                                    Clears at {w.price_per_as.amount} {w.price_per_as.currency}/AS
                                </span>
                            </div>
                        ))
                    )}
                    <div className="flex flex-wrap justify-between gap-2 border-t border-border pt-2 text-metadata text-muted tabular-nums">
                        <span>
                            Bundle value {a.bundle_value.amount} {a.bundle_value.currency} · Best
                            solo {a.single_brand_value.amount} {a.single_brand_value.currency}
                        </span>
                        {a.winners.length > 1 && lift !== null && (
                            <StatusBadge
                                tone="success"
                                label={`+${(lift * 100).toFixed(0)}% value vs. solo`}
                            />
                        )}
                        {a.winners.length > 1 && revenueLift !== null && revenueLift > 0 && (
                            <StatusBadge
                                tone="success"
                                label={`+${(revenueLift * 100).toFixed(0)}% revenue vs. solo`}
                            />
                        )}
                    </div>
                    {a.bids.length > 0 && (
                        <ul
                            className="flex flex-col gap-2 border-t border-border pt-2"
                            aria-label="Bids"
                        >
                            {a.bids.map((bid) => {
                                const exclusion = a.exclusions.find(
                                    (e) => e.campaign_id === bid.campaign_id,
                                );
                                return (
                                    <li
                                        key={bid.campaign_id}
                                        className="flex flex-wrap items-center justify-between gap-2 text-metadata"
                                    >
                                        <span>{bid.brand_name}</span>
                                        <span className="ml-auto text-muted tabular-nums">
                                            Bid {bid.bid_per_as.amount} {bid.bid_per_as.currency}/AS
                                        </span>
                                        {exclusion && (
                                            <span title={exclusion.detail || undefined}>
                                                <StatusBadge
                                                    tone="warning"
                                                    label={EXCLUSION_LABEL[exclusion.reason]}
                                                />
                                            </span>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>
            )}
        </Panel>
    );
}

function LatencyPanel({ view }: { view: ScreenView }) {
    const p50 = percentile(view.glassToGlassMs, 50);
    const p95 = percentile(view.glassToGlassMs, 95);
    const steps = view.lastTrace ? traceSteps(view.lastTrace) : [];
    const decisionMs = view.lastTrace ? serverDecisionMs(view.lastTrace) : null;
    return (
        <Panel
            title="Latency"
            aside={
                decisionMs !== null && (
                    <span className="text-metadata text-muted">
                        Server decision {fmtMs(decisionMs)}
                    </span>
                )
            }
        >
            {view.glassToGlassMs.length > 0 && (
                <div className="flex gap-6">
                    <Kpi
                        label="Glass-to-glass p50"
                        value={fmtMs(p50)}
                        tone={p50 !== null && p50 > 1500 ? "warning" : "neutral"}
                    />
                    <Kpi
                        label="Glass-to-glass p95"
                        value={fmtMs(p95)}
                        tone={p95 !== null && p95 > 2500 ? "warning" : "neutral"}
                    />
                </div>
            )}
            <ol className="mt-3 flex flex-col gap-1 text-metadata tabular-nums">
                {steps.map((s) => (
                    <li key={s.step} className="flex justify-between">
                        <span className="text-muted">{s.step}</span>
                        <span>{s.deltaMs > 0 ? `+${s.deltaMs}` : s.deltaMs} ms</span>
                    </li>
                ))}
            </ol>
        </Panel>
    );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone: Tone }) {
    return (
        <div className="flex flex-col">
            <span className="text-metadata text-muted uppercase">{label}</span>
            <span
                className={
                    tone === "warning"
                        ? "text-kpi font-semibold text-warning tabular-nums"
                        : "text-kpi font-semibold tabular-nums"
                }
            >
                {value}
            </span>
        </div>
    );
}
