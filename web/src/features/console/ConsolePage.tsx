import { ConsoleOutboundSchema, type ConsoleOutbound } from "@/contract";
import { backendWsUrl } from "@/lib/config";
import { decimalCl, moneyCl } from "@/lib/format";
import { createStore, useStore } from "@/lib/store";
import { TypedSocket } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Metric } from "@/shared/ui/metric";
import { AppShell, EmptyState, PageHeader } from "@/shared/ui/page";
import { Panel } from "@/shared/ui/Panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import { Monitor } from "lucide-react";
import { useEffect, useState } from "react";
import { consoleReducer, initialConsoleState, type ScreenView } from "./console-state";
import { CONNECTION, DEGRADATION, EXCLUSION, money, ms, SOURCE, stepLabel } from "./labels";
import { coBrandLift, percentile, serverDecisionMs, traceSteps } from "./metrics";

const pct = (ratio: number) => `${Math.round(ratio * 100)} %`;

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
        <AppShell>
            <PageHeader
                title="Consola"
                actions={
                    <>
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
            {Object.keys(screens).length === 0 ? (
                <Card>
                    <EmptyState
                        icon={<Monitor />}
                        title="Sin pantallas activas"
                        description="Inicia el agente o el simulador para ver la primera decisión."
                        action={
                            <Button asChild size="sm">
                                <a href="/screen?id=demo-01">Abrir pantalla</a>
                            </Button>
                        }
                    />
                </Card>
            ) : (
                Object.entries(screens).map(([screenId, view]) => (
                    <ScreenSection key={screenId} screenId={screenId} view={view} />
                ))
            )}
        </AppShell>
    );
}

function ScreenSection({ screenId, view }: { screenId: string; view: ScreenView }) {
    return (
        <section aria-label={`Pantalla ${screenId}`} className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
                <h2 className="font-mono text-compact text-muted">{screenId}</h2>
                {view.degradation.map((flag) => (
                    <Badge key={flag} tone="warning">
                        {DEGRADATION[flag]}
                    </Badge>
                ))}
            </div>
            <Headline view={view} />
            <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr_1fr]">
                <AudiencePanel view={view} />
                <AuctionPanel view={view} />
                <LatencyPanel view={view} />
                <ReceiptPanel view={view} />
                <MarketPanel view={view} />
            </div>
        </section>
    );
}

/** The four numbers the demo is judged on, in one row. */
function Headline({ view }: { view: ScreenView }) {
    const a = view.auction?.moment_id === view.moment?.moment_id ? view.auction : null;
    const revenueLift =
        a?.bundle_revenue && a.single_brand_revenue && a.winners.length > 1
            ? coBrandLift(a.bundle_revenue.amount, a.single_brand_revenue.amount)
            : null;
    const p50 = percentile(view.glassToGlassMs, 50);
    // Session total: presentation only (ADR 0008 D5); each amount stays a decimal string.
    const billed = view.receipts.reduce((sum, r) => sum + Number(r.total.amount), 0);
    return (
        <Card className="grid grid-cols-2 gap-6 p-4 md:grid-cols-4">
            <Metric label="Cobertura de la audiencia" value={a ? pct(a.coverage) : "—"} />
            <Metric
                label="Ingreso vs. una sola marca"
                value={revenueLift === null ? "—" : `+${pct(revenueLift)}`}
            />
            <Metric
                label="Cámara a pantalla (p50)"
                value={ms(p50)}
                tone={p50 !== null && p50 > 1500 ? "warning" : "default"}
                hint={p50 !== null && p50 > 1500 ? "Sobre la meta de 1,5 s" : undefined}
            />
            <Metric label="Cobrado en la sesión" value={moneyCl(String(billed))} />
        </Card>
    );
}

function AudiencePanel({ view }: { view: ScreenView }) {
    const m = view.moment;
    return (
        <Panel title="Audiencia" aside={m && `${m.people} personas · ${m.looking} mirando`}>
            {!m ? (
                <p className="text-compact text-muted">Sin momento todavía.</p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {m.minor_likely.value && (
                        <li>
                            <Badge tone="warning" appearance="soft">
                                Posible menor: categorías restringidas bloqueadas
                            </Badge>
                        </li>
                    )}
                    {m.segments.length === 0 && (
                        <li className="text-compact text-muted">Sin intereses detectados.</li>
                    )}
                    {m.segments.map((s) => (
                        <li key={s.segment_id} className="flex flex-col gap-1.5">
                            <div className="flex justify-between gap-2 text-compact">
                                <span>{s.label}</span>
                                <span className="text-muted tabular-nums">
                                    {pct(s.score)} · {s.sources.map((x) => SOURCE[x]).join(", ")}
                                </span>
                            </div>
                            <div className="h-1 rounded-full bg-surface-pressed">
                                <div
                                    className="h-full rounded-full bg-ink"
                                    style={{ width: pct(s.score).replace(" ", "") }}
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
    const holdUntil = a?.swap === "held" ? a.trace.swap_not_before : null;
    return (
        <Panel title="Subasta" aside={a && `${a.bids.length} pujas`}>
            {!a ? (
                <p className="text-compact text-muted">Sin subasta todavía.</p>
            ) : (
                <div className="flex flex-col gap-4">
                    {a.winners.length === 0 ? (
                        <Badge appearance="soft">Sin ganador: aviso de la casa</Badge>
                    ) : (
                        <ul className="flex flex-col gap-1">
                            {a.winners.map((w) => (
                                <li key={w.campaign_id} className="flex justify-between gap-2">
                                    <span className="font-semibold">{w.brand_name}</span>
                                    <span className="tabular-nums">
                                        {money(w.price_per_as)}
                                        <span className="text-muted"> por s de atención</span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                    {holdUntil && (
                        <p className="text-compact text-muted">
                            Cambia a las {new Date(holdUntil).toLocaleTimeString("es-CL")} por el
                            tiempo mínimo en pantalla.
                        </p>
                    )}
                    <p className="text-compact text-muted tabular-nums">
                        Valor de la canasta {money(a.bundle_value)} · mejor marca sola{" "}
                        {money(a.single_brand_value)}
                        {a.winners.length > 1 && lift !== null && (
                            <span className="text-success"> (+{pct(lift)})</span>
                        )}
                    </p>
                    {a.bids.length > 0 && (
                        <Table aria-label="Pujas">
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Marca</TableHead>
                                    <TableHead className="text-right">Puja</TableHead>
                                    <TableHead>Resultado</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {a.bids.map((bid) => {
                                    const exclusion = a.exclusions.find(
                                        (e) => e.campaign_id === bid.campaign_id,
                                    );
                                    const won = a.winners.some(
                                        (w) => w.campaign_id === bid.campaign_id,
                                    );
                                    return (
                                        <TableRow key={bid.campaign_id}>
                                            <TableCell>{bid.brand_name}</TableCell>
                                            <TableCell className="text-right">
                                                {money(bid.bid_per_as)}
                                            </TableCell>
                                            <TableCell title={exclusion?.detail || undefined}>
                                                {exclusion ? (
                                                    <Badge tone="warning">
                                                        {EXCLUSION[exclusion.reason]}
                                                    </Badge>
                                                ) : won ? (
                                                    <Badge tone="success">Gana</Badge>
                                                ) : (
                                                    <Badge>Pierde</Badge>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    )}
                </div>
            )}
        </Panel>
    );
}

function LatencyPanel({ view }: { view: ScreenView }) {
    const p95 = percentile(view.glassToGlassMs, 95);
    const steps = view.lastTrace ? traceSteps(view.lastTrace) : [];
    const decisionMs = view.lastTrace ? serverDecisionMs(view.lastTrace) : null;
    return (
        <Panel title="Latencia" aside={decisionMs !== null && `Decisión ${ms(decisionMs)}`}>
            {steps.length === 0 ? (
                <p className="text-compact text-muted">Sin mediciones todavía.</p>
            ) : (
                <>
                    <ol className="flex flex-col gap-1 text-compact tabular-nums">
                        {steps.map((s) => (
                            <li key={s.step} className="flex justify-between">
                                <span className="text-muted">{stepLabel(s.step)}</span>
                                <span>{s.deltaMs > 0 ? `+${s.deltaMs}` : s.deltaMs} ms</span>
                            </li>
                        ))}
                    </ol>
                    <p className="mt-3 border-t border-border-subtle pt-3 text-compact text-muted tabular-nums">
                        p95 cámara a pantalla{" "}
                        <span className={p95 !== null && p95 > 2500 ? "text-warning" : "text-fg"}>
                            {ms(p95)}
                        </span>
                    </p>
                </>
            )}
        </Panel>
    );
}

/** The last moment receipt: who looked, for how long, and what each brand paid. */
function ReceiptPanel({ view }: { view: ScreenView }) {
    const [last] = view.receipts;
    return (
        <Panel title="Recibo del momento" className="lg:col-span-1">
            {!last ? (
                <p className="text-compact text-muted">Ningún aviso cobrado todavía.</p>
            ) : (
                <div className="flex flex-col gap-3">
                    <p className="text-compact text-muted tabular-nums">
                        {last.people} personas · {last.looking} miraron ·{" "}
                        {decimalCl(last.attention_seconds, 1)} s de atención ·{" "}
                        {decimalCl(last.played_s, 1)} s al aire
                    </p>
                    {last.total.amount === "0" ? (
                        <Badge tone="info" appearance="soft">
                            Cobrado $0: nadie miró
                        </Badge>
                    ) : (
                        <ul className="flex flex-col gap-1 text-compact tabular-nums">
                            {last.charges.map((c) => (
                                <li key={c.campaign_id} className="flex justify-between gap-2">
                                    <span>{c.brand_name}</span>
                                    <span>
                                        <span className="text-muted">
                                            {decimalCl(c.attention_seconds, 1)} s ×{" "}
                                            {money(c.price_per_as)} ={" "}
                                        </span>
                                        <span className="font-semibold">{money(c.amount)}</span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
            {view.redemptions.length > 0 && (
                <ul
                    className="mt-3 flex flex-col gap-1 border-t border-border-subtle pt-3"
                    aria-label="Canjes"
                >
                    {view.redemptions.map((r) => (
                        <li key={`${r.code}:${r.redeemed_at}`}>
                            <Badge tone="success">
                                Canje {r.code} · {r.brands.join(" + ")}
                            </Badge>
                        </li>
                    ))}
                </ul>
            )}
        </Panel>
    );
}

/** Live bids, stock and budgets (reserved while on air). */
function MarketPanel({ view }: { view: ScreenView }) {
    const book = view.book;
    return (
        <Panel title="Mercado" className="lg:col-span-2">
            {!book ? (
                <p className="text-compact text-muted">Aparece tras la primera decisión.</p>
            ) : (
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Marca</TableHead>
                            <TableHead>Estado</TableHead>
                            <TableHead className="text-right">Puja</TableHead>
                            <TableHead className="text-right">Gastado</TableHead>
                            <TableHead className="text-right">Reservado</TableHead>
                            <TableHead className="text-right">Disponible</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {book.lines.map((l) => (
                            <TableRow key={l.campaign_id}>
                                <TableCell className="font-medium">{l.brand_name}</TableCell>
                                <TableCell>
                                    {!l.in_stock ? (
                                        <Badge tone="danger">Sin stock</Badge>
                                    ) : l.on_air ? (
                                        <Badge tone="success">Al aire</Badge>
                                    ) : (
                                        <Badge>En espera</Badge>
                                    )}
                                </TableCell>
                                <TableCell className="text-right">
                                    ×{decimalCl(l.multiplier, 1)}
                                </TableCell>
                                <TableCell className="text-right">{money(l.spent)}</TableCell>
                                <TableCell className="text-right">{money(l.reserved)}</TableCell>
                                <TableCell className="text-right">{money(l.remaining)}</TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            )}
        </Panel>
    );
}
