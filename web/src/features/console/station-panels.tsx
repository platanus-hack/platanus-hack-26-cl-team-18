import { CreativeView } from "@/features/screen";
import { demoKey, rememberedDemoKey } from "@/lib/demo";
import { decimalCl, moneyCl } from "@/lib/format";
import { setBidMultiplier, setStock } from "@/lib/market-api";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Metric } from "@/shared/ui/metric";
import { Panel } from "@/shared/ui/Panel";
import { Minus, Plus } from "lucide-react";
import { useState } from "react";
import { bidRows, budgetLeft, splitRows, stepMultiplier, type BidRow } from "./bids";
import type { ScreenView } from "./console-state";
import { count, money, ms, signedPct } from "./labels";
import { coBrandLift, percentile, serverDecisionMs } from "./metrics";
import { pct } from "./panels";

/** What the TV shows now: the same renderer as the player, never connected as one. */
export function OnAirPanel({ view }: { view: ScreenView }) {
    const onAir = view.onAir;
    const offer = onAir?.spec.offer ?? null;
    return (
        <Panel
            title="Al aire"
            aside={onAir && (onAir.auction_id === null ? "aviso de la casa" : "aviso pagado")}
        >
            <div className="flex flex-col gap-3">
                <div
                    className="relative aspect-video overflow-hidden rounded-control bg-ink"
                    aria-label="Lo que muestra la pantalla"
                >
                    {onAir ? (
                        <CreativeView spec={onAir.spec} playKey={onAir.creative_id} />
                    ) : (
                        <p className="grid h-full place-items-center text-compact text-white/70">
                            Esperando la pantalla…
                        </p>
                    )}
                </div>
                {onAir && onAir.auction_id !== null && (
                    <p className="text-compact tabular-nums">
                        <span className="font-medium">
                            {onAir.spec.slots.map((s) => s.brand_name).join(" + ")}
                        </span>
                        {offer && (
                            <span className="text-muted">
                                {" "}
                                · {offer.label} {moneyCl(offer.price.amount)}
                            </span>
                        )}
                    </p>
                )}
            </div>
        </Panel>
    );
}

/** Every brand, its bid and why it won or lost; demo controls when this browser has the key. */
export function BidsPanel({ view, screen }: { view: ScreenView; screen: string }) {
    const book = view.book;
    const auction = view.auction;
    const controls = (demoKey() ?? rememberedDemoKey()) !== null;
    const [error, setError] = useState<string | null>(null);
    if (!book) {
        return (
            <Panel title="Subasta">
                <p className="text-compact text-muted">Cargando marcas…</p>
            </Panel>
        );
    }
    const rows = bidRows(book, auction);
    const { shown, idle } = splitRows(rows);
    const reasons = rows.filter((row) => row.why !== null);
    const lift =
        auction && auction.winners.length > 1
            ? coBrandLift(auction.bundle_value.amount, auction.single_brand_value.amount)
            : null;
    return (
        <Panel
            title="Subasta"
            aside={auction ? count(auction.bids.length, "puja", "pujas") : "esperando a alguien"}
        >
            <div className="flex flex-col gap-3">
                {auction && (
                    <p className="text-compact text-muted tabular-nums">
                        Canasta {money(auction.bundle_value)} · mejor marca sola{" "}
                        {money(auction.single_brand_value)}
                        {lift !== null && (
                            <span className={lift < 0 ? "text-warning" : "text-success"}>
                                {" "}
                                ({signedPct(lift)})
                            </span>
                        )}
                    </p>
                )}
                <ul className="flex flex-col divide-y divide-border-subtle" aria-label="Marcas">
                    {shown.map((row) => (
                        <BidLine
                            key={row.campaignId}
                            row={row}
                            screen={screen}
                            controls={controls}
                            onError={setError}
                        />
                    ))}
                </ul>
                {idle.length > 0 && (
                    <p className="text-compact text-muted" aria-label="Sin interés en este momento">
                        Sin interés en este momento: {idle.map((row) => row.brand).join(", ")}
                    </p>
                )}
                {error && (
                    <p className="text-compact text-danger" role="status">
                        No se aplicó: {error}
                    </p>
                )}
                {reasons.length > 0 && (
                    <div className="border-t border-border-subtle pt-3">
                        <p className="mb-1 text-metadata text-muted">Por qué</p>
                        <ul className="flex flex-col gap-1 text-compact">
                            {reasons.map((row) => (
                                <li key={row.campaignId}>{row.why}</li>
                            ))}
                        </ul>
                    </div>
                )}
                {!controls && (
                    <p className="text-metadata text-muted">
                        Para mover pujas y stock aquí, abre una vez /demo/&lt;clave&gt; en este
                        navegador.
                    </p>
                )}
            </div>
        </Panel>
    );
}

function BidLine({
    row,
    screen,
    controls,
    onError,
}: {
    row: BidRow;
    screen: string;
    controls: boolean;
    onError: (message: string | null) => void;
}) {
    const run = (action: Promise<unknown>) => {
        action.then(
            () => {
                onError(null);
            },
            (reason: unknown) => {
                onError(String(reason));
            },
        );
    };
    const step = (direction: 1 | -1) => {
        const next = stepMultiplier(row.multiplier, direction);
        if (next !== null) run(setBidMultiplier(row.campaignId, next, row.multiplierSeq));
    };
    return (
        <li
            className="grid grid-cols-[minmax(7rem,1fr)_4.5rem_auto_auto_6.5rem] items-center gap-x-3 py-1.5"
            data-brand={row.brand}
        >
            <span className="flex min-w-0 flex-col gap-1">
                <span className="flex flex-wrap items-center gap-x-2 font-medium">
                    <span className="flex items-center gap-2 whitespace-nowrap">
                        <span
                            aria-hidden
                            className="size-2.5 shrink-0 rounded-full"
                            style={{ background: row.accent ?? "var(--color-border)" }}
                        />
                        {row.brand}
                    </span>
                    {row.onAir && <Badge tone="success">al aire</Badge>}
                </span>
                <span
                    className="h-0.5 w-full max-w-32 rounded-full bg-border-subtle"
                    title={`Presupuesto restante ${money(row.remaining)}`}
                    aria-label={`Presupuesto restante ${money(row.remaining)}`}
                >
                    <span
                        className="block h-full rounded-full bg-accent"
                        style={{ width: `${Math.round(budgetLeft(row) * 100)}%` }}
                    />
                </span>
            </span>
            <span className="text-right text-compact tabular-nums">
                {row.bidPerAs ? `${money(row.bidPerAs)}/s` : "—"}
            </span>
            <span className="flex items-center gap-0.5 text-compact tabular-nums">
                {controls && (
                    <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Bajar puja de ${row.brand}`}
                        disabled={stepMultiplier(row.multiplier, -1) === null}
                        onClick={() => {
                            step(-1);
                        }}
                    >
                        <Minus aria-hidden />
                    </Button>
                )}
                ×{decimalCl(row.multiplier, 1)}
                {controls && (
                    <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Subir puja de ${row.brand}`}
                        disabled={stepMultiplier(row.multiplier, 1) === null}
                        onClick={() => {
                            step(1);
                        }}
                    >
                        <Plus aria-hidden />
                    </Button>
                )}
                {row.autobid && <Badge tone="info">auto</Badge>}
            </span>
            <span>
                {controls ? (
                    <button
                        type="button"
                        role="switch"
                        aria-checked={row.inStock}
                        aria-label={`Stock de ${row.brand}`}
                        className={
                            row.inStock
                                ? "rounded-control border border-control-edge px-2 py-0.5 text-metadata whitespace-nowrap"
                                : "rounded-control border border-(--state-danger-edge) px-2 py-0.5 text-metadata whitespace-nowrap text-danger"
                        }
                        onClick={() => {
                            run(setStock(screen, row.brandId, !row.inStock));
                        }}
                    >
                        {row.inStock ? "Con stock" : "Sin stock"}
                    </button>
                ) : (
                    !row.inStock && <Badge tone="danger">Sin stock</Badge>
                )}
            </span>
            <span className="text-right">
                {row.result ? (
                    <Badge tone={row.result.tone}>{row.result.label}</Badge>
                ) : (
                    <span className="text-metadata text-muted">—</span>
                )}
            </span>
        </li>
    );
}

/** The numbers the demo is judged on, and the last receipts. */
export function SessionPanel({ view }: { view: ScreenView }) {
    const auction = view.auction;
    const p50 = percentile(view.glassToGlassMs, 50);
    const p95 = percentile(view.glassToGlassMs, 95);
    const decision = view.lastTrace ? serverDecisionMs(view.lastTrace) : null;
    const revenueLift =
        auction?.bundle_revenue && auction.single_brand_revenue && auction.winners.length > 1
            ? coBrandLift(auction.bundle_revenue.amount, auction.single_brand_revenue.amount)
            : null;
    // Session total: presentation only (ADR 0008 D5); each amount stays a decimal string.
    const billed = view.receipts.reduce((sum, r) => sum + Number(r.total.amount), 0);
    return (
        <Panel title="Sesión">
            <div className="grid grid-cols-2 gap-4">
                <Metric label="Cobertura" value={auction ? pct(auction.coverage) : "—"} />
                <Metric label="Cobrado" value={moneyCl(String(billed))} />
                <Metric
                    label="vs. una marca"
                    value={revenueLift === null ? "—" : signedPct(revenueLift)}
                />
                <Metric
                    label="Cámara → pantalla (p50)"
                    value={ms(p50)}
                    tone={p50 !== null && p50 > 1500 ? "warning" : "default"}
                    hint={
                        p50 === null
                            ? undefined
                            : p50 > 1500
                              ? `Sobre la meta de 1,5 s · p95 ${ms(p95)}`
                              : `p95 ${ms(p95)}`
                    }
                />
            </div>
            {decision !== null && (
                <p className="mt-3 text-metadata text-muted tabular-nums">
                    Decisión del servidor {ms(decision)}
                </p>
            )}
            <div className="mt-4 border-t border-border-subtle pt-3">
                <p className="mb-2 text-metadata text-muted">Recibos</p>
                {view.receipts.length === 0 ? (
                    <p className="text-compact text-muted">Ningún aviso cobrado todavía.</p>
                ) : (
                    <ul className="flex flex-col gap-2 text-compact tabular-nums">
                        {view.receipts.slice(0, 4).map((r) => (
                            <li key={`${r.creative_id}:${r.auction_id}`}>
                                {r.total.amount === "0" ? (
                                    <Badge tone="info">$0: nadie miró</Badge>
                                ) : (
                                    <span>
                                        {r.charges
                                            .map((c) => `${c.brand_name} ${money(c.amount)}`)
                                            .join(" · ")}
                                        <span className="text-muted">
                                            {" "}
                                            · {decimalCl(r.attention_seconds, 1)} s de atención
                                        </span>
                                    </span>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
                {view.redemptions.slice(0, 3).map((r) => (
                    <Badge key={`${r.code}:${r.redeemed_at}`} tone="success" className="mt-2">
                        Canje {r.code} · {r.brands.join(" + ")}
                    </Badge>
                ))}
            </div>
        </Panel>
    );
}

/** The loop narrated: moment → auction → on air → receipt, newest first. */
export function Timeline({ view }: { view: ScreenView }) {
    return (
        <Panel title="Actividad" aside={view.events.length > 0 && "en vivo"}>
            {view.events.length === 0 ? (
                <p className="text-compact text-muted">
                    Aquí aparece cada momento, subasta, aviso al aire y recibo.
                </p>
            ) : (
                <ol className="flex max-h-56 flex-col gap-1 overflow-y-auto text-compact">
                    {view.events.map((event, index) => (
                        <li key={`${event.at}:${index}`} className="flex gap-3">
                            <span className="w-16 shrink-0 text-muted tabular-nums">
                                {event.at
                                    ? new Date(event.at).toLocaleTimeString("es-CL", {
                                          hour12: false,
                                      })
                                    : ""}
                            </span>
                            <span
                                className={
                                    event.tone === "success"
                                        ? "text-success"
                                        : event.tone === "warning"
                                          ? "text-warning"
                                          : "text-fg"
                                }
                            >
                                {event.text}
                            </span>
                        </li>
                    ))}
                </ol>
            )}
        </Panel>
    );
}
