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
import { CONNECTION, count, DEGRADATION, money, ms, signedPct } from "./labels";
import { LiveInputStatus } from "./LiveInputStatus";
import { LivePanel } from "./LivePanel";
import { coBrandLift, percentile } from "./metrics";
import { AuctionPanel, AudiencePanel, LatencyPanel, pct } from "./panels";

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
            <LivePanel screen={screenFilter ?? "demo-01"} />
            <LiveInputStatus screen={screenFilter ?? "demo-01"} />
            {shownScreens.length === 0 ? (
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
                shownScreens.map(([screenId, view]) => (
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
                <h2 className="text-compact text-muted">
                    Pantalla <span className="font-mono text-fg">{screenId}</span>
                </h2>
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
                value={revenueLift === null ? "—" : signedPct(revenueLift)}
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
                        {count(last.people, "persona", "personas")} · {last.looking} miraron ·{" "}
                        {decimalCl(last.attention_seconds, 1)} s de atención ·{" "}
                        {decimalCl(last.played_s, 1)} s al aire
                    </p>
                    {last.filtered.length > 0 && (
                        <p className="text-metadata text-muted tabular-nums">
                            No cobrado:{" "}
                            {last.filtered.map((f) => `${f.reason} ${f.seconds} s`).join(" · ")}
                        </p>
                    )}
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
                <p className="text-compact text-muted">Sin datos del mercado todavía.</p>
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
                                <TableCell
                                    className="text-right"
                                    title={l.autobid_reason ?? undefined}
                                >
                                    ×{decimalCl(l.multiplier, 1)}
                                    {l.autobid && <Badge tone="info">auto</Badge>}
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
