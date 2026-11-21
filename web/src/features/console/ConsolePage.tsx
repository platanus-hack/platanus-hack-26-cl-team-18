import { ConsoleOutboundSchema, type ConsoleOutbound, type ExclusionReason } from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket, type ConnectionState } from "@/lib/ws/typed-socket";
import { Empty, Panel } from "@/shared/ui/Panel";
import { StatusBadge, type Tone } from "@/shared/ui/StatusBadge";
import { WorkspaceHeader } from "@/shared/ui/WorkspaceHeader";
import { Monitor } from "lucide-react";
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

const DEMO_BRANDS: Readonly<Record<string, string>> = {
    "cafe-patio": "Café Patio",
    voltio: "Voltio",
    rayo: "Rayo",
    "malta-sur": "Malta Sur",
};

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
    const screenFilter = new URLSearchParams(window.location.search).get("screen");
    const shownScreens = Object.entries(screens).filter(
        ([screenId]) => screenFilter === null || screenId === screenFilter,
    );

    return (
        <div className="paper-theme">
            <WorkspaceHeader />
            <main className="workspace-content flex flex-col gap-6">
                <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-6">
                    <div>
                        <p className="eyebrow mb-2">Operación en vivo</p>
                        <h1 className="page-heading">Console</h1>
                        <p className="mt-2 text-body text-muted">
                            Cada momento. Cada decisión. Cada segundo de atención.
                        </p>
                    </div>
                    <div className="flex items-center gap-3">
                        {lastError && <StatusBadge tone="danger" label={lastError} />}
                        <StatusBadge tone={tone} label={label} />
                    </div>
                </header>
                {shownScreens.length === 0 ? (
                    <Panel title="Waiting for screens">
                        <div className="flex flex-col items-center gap-4 py-14 text-center">
                            <span className="workspace-mark">
                                <Monitor className="size-5" aria-hidden />
                            </span>
                            <div>
                                <p className="mb-2 text-section font-medium">
                                    Todo listo para el próximo momento
                                </p>
                                <Empty>
                                    No screen activity yet. Start the agent or the simulator.
                                </Empty>
                            </div>
                            <a
                                className="rounded-control border border-border px-4 py-2 text-body text-accent hover:bg-accent-soft"
                                href="/screen?id=demo-01"
                            >
                                Abrir pantalla
                            </a>
                        </div>
                    </Panel>
                ) : (
                    shownScreens.map(([screenId, view]) => (
                        <ScreenRow key={screenId} screenId={screenId} view={view} />
                    ))
                )}
            </main>
        </div>
    );
}

function ScreenRow({ screenId, view }: { screenId: string; view: ScreenView }) {
    const moment = view.moment;
    const auction = view.auction?.moment_id === moment?.moment_id ? view.auction : null;
    const redemption = view.redemptions[0];
    return (
        <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr_1fr]">
            {moment && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-container border border-border bg-surface p-4 text-section lg:col-span-3">
                    <strong>{moment.looking} mirando</strong>
                    {auction && (
                        <>
                            <span aria-hidden>→</span>
                            <span>
                                {auction.swap === "held" ? "Próximo: " : ""}
                                {auction.winners.map((winner) => winner.brand_name).join(" + ") ||
                                    "Anuncio de la tienda"}
                            </span>
                            {auction.offer && (
                                <strong className="tabular-nums">
                                    {auction.offer.label} · $
                                    {Number(auction.offer.price.amount).toLocaleString("es-CL")}
                                </strong>
                            )}
                        </>
                    )}
                    {redemption && (
                        <StatusBadge
                            tone="success"
                            label={`Último canje · ${redemption.brands.length} marcas acreditadas · ${redemption.code}`}
                        />
                    )}
                </div>
            )}
            <MomentPanel screenId={screenId} view={view} />
            <AuctionPanel view={view} />
            <LatencyPanel view={view} />
            <ReceiptPanel view={view} />
            <MarketPanel view={view} />
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
                    {m.held_products.map((product) => (
                        <li key={`${product.brand_ref ?? ""}:${product.category}`}>
                            <StatusBadge
                                tone="info"
                                label={`En mano · ${(product.brand_ref && DEMO_BRANDS[product.brand_ref]) || product.brand_ref || product.category}`}
                            />
                        </li>
                    ))}
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
    const perception = view.lastTrace?.perception;
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
            {perception && (
                <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-metadata tabular-nums">
                    <dt className="text-muted">Detector</dt>
                    <dd>{perception.detector}</dd>
                    <dt className="text-muted">Inference + fallback</dt>
                    <dd>{fmtMs(perception.inference_ms)}</dd>
                    {perception.round_trip_ms !== null && (
                        <>
                            <dt className="text-muted">GPU round trip</dt>
                            <dd>{fmtMs(perception.round_trip_ms)}</dd>
                        </>
                    )}
                    {perception.primary_attempt_ms !== null && (
                        <>
                            <dt className="text-muted">GPU attempt before fallback</dt>
                            <dd>{fmtMs(perception.primary_attempt_ms)}</dd>
                        </>
                    )}
                </dl>
            )}
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

/** The last moment receipt: who looked, for how long, and what each brand paid. */
function ReceiptPanel({ view }: { view: ScreenView }) {
    const [last] = view.receipts;
    // Session total: presentation only (ADR 0008 D5); each amount stays a decimal string.
    const session = view.receipts.reduce((sum, r) => sum + Number(r.total.amount), 0);
    return (
        <Panel
            title="Moment receipt"
            aside={
                view.receipts.length > 0 && (
                    <span className="text-metadata text-muted tabular-nums">
                        Session {session} {last?.total.currency}
                    </span>
                )
            }
        >
            {!last ? (
                <Empty>No play has been billed yet.</Empty>
            ) : (
                <div className="flex flex-col gap-2">
                    <p className="tabular-nums">
                        {last.people} people · {last.looking} looked · {last.attention_seconds} AS ·{" "}
                        {last.played_s} s on screen
                    </p>
                    {last.total.amount === "0" ? (
                        <StatusBadge tone="info" label="Charged $0: nobody looked" />
                    ) : (
                        <ul className="flex flex-col gap-1">
                            {last.charges.map((c) => (
                                <li
                                    key={c.campaign_id}
                                    className="flex justify-between tabular-nums"
                                >
                                    <span>{c.brand_name}</span>
                                    <span>
                                        {c.attention_seconds} AS × {c.price_per_as.amount} ={" "}
                                        <strong>
                                            ${c.amount.amount} {c.amount.currency}
                                        </strong>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
            {view.redemptions.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1" aria-label="Redemptions">
                    {view.redemptions.map((r) => (
                        <li key={`${r.code}:${r.redeemed_at}`}>
                            <StatusBadge
                                tone="success"
                                label={`Canjeado · ${r.brands.join(" + ")} · ${r.code}`}
                            />
                        </li>
                    ))}
                </ul>
            )}
        </Panel>
    );
}

/** Live bids (the bidding war), stock (the clerk) and budgets (reserved while on air). */
function MarketPanel({ view }: { view: ScreenView }) {
    const book = view.book;
    return (
        <Panel
            title="Market"
            aside={<span className="text-metadata text-muted">/advertiser · /clerk</span>}
        >
            {!book ? (
                <Empty>The book appears after the first decision.</Empty>
            ) : (
                <table className="w-full text-metadata tabular-nums">
                    <thead className="text-muted">
                        <tr>
                            <th className="text-left font-normal">Brand</th>
                            <th className="text-right font-normal">Bid ×</th>
                            <th className="text-right font-normal">Spent</th>
                            <th className="text-right font-normal">Held</th>
                            <th className="text-right font-normal">Left</th>
                        </tr>
                    </thead>
                    <tbody>
                        {book.lines.map((l) => (
                            <tr key={l.campaign_id}>
                                <td>
                                    {l.brand_name}
                                    {l.on_air && <span className="ml-1 text-success">●</span>}
                                    {!l.in_stock && (
                                        <span className="ml-1 text-warning">out of stock</span>
                                    )}
                                </td>
                                <td className="text-right">{l.multiplier}</td>
                                <td className="text-right">{l.spent.amount}</td>
                                <td className="text-right">{l.reserved.amount}</td>
                                <td className="text-right">{l.remaining.amount}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
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
