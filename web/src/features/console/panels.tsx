import type { AuctionConditions } from "@/contract";
import { categoryLabel } from "@/lib/categories";
import { Badge } from "@/shared/ui/badge";
import { Panel } from "@/shared/ui/Panel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import type { ScreenView } from "./console-state";
import {
    affinityRoutes,
    count,
    EXCLUSION,
    money,
    ms,
    signedPct,
    SOURCE,
    stepLabel,
} from "./labels";
import { coBrandLift, percentile, serverDecisionMs, traceSteps } from "./metrics";

/** Console panels shared with the dev view (/dev): audience, auction, latency. */

export const pct = (ratio: number) => `${Math.round(ratio * 100)} %`;

/** Display names for held-product slugs (the station's catalog, app/bootstrap/demo_catalog_chile.py). */
export const DEMO_BRANDS: Readonly<Record<string, string>> = {
    score: "Score",
    "red-bull": "Red Bull",
    monster: "Monster",
    "juan-valdez": "Juan Valdez",
    nescafe: "Nescafé",
    "pronto-copec": "Pronto Copec",
    "coca-cola": "Coca-Cola",
    pepsi: "Pepsi",
    cachantun: "Cachantun",
    benedictino: "Benedictino",
    lays: "Lay's",
    "marco-polo": "Marco Polo",
    "super-8": "Super 8",
    ambrosoli: "Ambrosoli",
    "bilz-y-pap": "Bilz y Pap",
    powerade: "Powerade",
    gatorade: "Gatorade",
    colun: "Colun",
    soprole: "Soprole",
    watts: "Watt's",
    "andina-del-valle": "Andina del Valle",
    savory: "Savory",
    bresler: "Bresler",
};

export function AudiencePanel({ view }: { view: ScreenView }) {
    const m = view.moment;
    return (
        <Panel
            title="Audiencia"
            aside={m && `${count(m.people, "persona", "personas")} · ${m.looking} mirando`}
        >
            {!m ? (
                <p className="text-compact text-muted">Sin momento todavía.</p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {m.held_products.map((product) => (
                        <li key={`${product.brand_ref ?? ""}:${product.category}`}>
                            <Badge tone="info" appearance="soft">
                                En mano ·{" "}
                                {(product.brand_ref && DEMO_BRANDS[product.brand_ref]) ||
                                    product.brand_ref ||
                                    product.category}
                            </Badge>
                        </li>
                    ))}
                    {(m.minor_likely.value || m.minor_likely.uncertain) && (
                        <li>
                            <Badge
                                tone="warning"
                                appearance="soft"
                                className="rounded-container whitespace-normal"
                            >
                                {m.minor_likely.uncertain
                                    ? "Seguridad por confirmar: categorías restringidas bloqueadas"
                                    : "Posible menor: categorías restringidas bloqueadas"}
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
                            <div className="h-0.5 rounded-full bg-border-subtle">
                                <div
                                    className="h-full rounded-full bg-accent"
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

const DAYPART: Readonly<Record<string, string>> = {
    morning: "Mañana",
    midday: "Mediodía",
    afternoon: "Tarde",
    evening: "Atardecer",
    night: "Noche",
};
const WEATHER: Readonly<Record<string, string>> = {
    cold: "Frío",
    mild: "Templado",
    warm: "Cálido",
    hot: "Calor",
    rain: "Lluvia",
};

/** "Tarde · 33 °C Calor (simulado)" and each category the context lifts or lowers. */
export function ContextLine({ conditions }: { conditions: AuctionConditions }) {
    const head = [
        conditions.daypart && DAYPART[conditions.daypart],
        conditions.temperature_c != null &&
            `${Math.round(conditions.temperature_c)} °C ${conditions.weather.map((w) => WEATHER[w]).join(" + ")}`,
    ].filter(Boolean);
    if (head.length === 0 && conditions.factors.length === 0) return null;
    return (
        <div className="flex flex-col gap-1 text-compact" aria-label="Contexto de la subasta">
            <span className="text-muted">
                Contexto: {head.join(" · ") || "sin datos"}
                {conditions.weather_source === "override" && " (simulado)"}
                {conditions.weather_source === "none" && " · sin clima"}
            </span>
            {conditions.factors.length > 0 && (
                <span className="flex flex-wrap gap-1">
                    {conditions.factors.map((f) => (
                        <Badge
                            key={f.category}
                            appearance="soft"
                            tone={Number(f.multiplier) >= 1 ? "success" : "warning"}
                            title={f.reasons.join(" · ")}
                        >
                            {categoryLabel(f.category)} ×{f.multiplier}
                        </Badge>
                    ))}
                </span>
            )}
        </div>
    );
}

export function AuctionPanel({ view }: { view: ScreenView }) {
    const a = view.auction?.moment_id === view.moment?.moment_id ? view.auction : null;
    const lift = a ? coBrandLift(a.bundle_value.amount, a.single_brand_value.amount) : null;
    const holdUntil = a?.swap === "held" ? a.trace.swap_not_before : null;
    return (
        <Panel title="Subasta" aside={a && count(a.bids.length, "puja", "pujas")}>
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
                    {a.conditions && <ContextLine conditions={a.conditions} />}
                    <p className="text-compact text-muted tabular-nums">
                        Valor de la canasta {money(a.bundle_value)} · mejor marca sola{" "}
                        {money(a.single_brand_value)}
                        {a.winners.length > 1 && lift !== null && (
                            <span className={lift < 0 ? "text-warning" : "text-success"}>
                                {" "}
                                ({signedPct(lift)})
                            </span>
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
                                    const routes = affinityRoutes(bid.matches);
                                    return (
                                        <TableRow key={bid.campaign_id}>
                                            <TableCell>
                                                {bid.brand_name}
                                                {bid.context_multiplier && (
                                                    <span
                                                        className="ml-1 text-metadata text-muted tabular-nums"
                                                        aria-label="Multiplicador de contexto"
                                                    >
                                                        ×{bid.context_multiplier}
                                                    </span>
                                                )}
                                                {routes && (
                                                    <span
                                                        className="block text-metadata text-muted"
                                                        aria-label="Coincidencia por afinidad de segmento"
                                                    >
                                                        {routes}
                                                    </span>
                                                )}
                                            </TableCell>
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

export function LatencyPanel({ view }: { view: ScreenView }) {
    const p95 = percentile(view.glassToGlassMs, 95);
    const steps = view.lastTrace ? traceSteps(view.lastTrace) : [];
    const decisionMs = view.lastTrace ? serverDecisionMs(view.lastTrace) : null;
    const perception = view.lastTrace?.perception;
    return (
        <Panel title="Latencia" aside={decisionMs !== null && `Decisión ${ms(decisionMs)}`}>
            {perception && (
                <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-compact tabular-nums">
                    <dt className="text-muted">Detector</dt>
                    <dd>{perception.detector}</dd>
                    <dt className="text-muted">Inferencia + respaldo</dt>
                    <dd>{ms(perception.inference_ms)}</dd>
                    {perception.round_trip_ms !== null && (
                        <>
                            <dt className="text-muted">Ida y vuelta GPU</dt>
                            <dd>{ms(perception.round_trip_ms)}</dd>
                        </>
                    )}
                    {perception.primary_attempt_ms !== null && (
                        <>
                            <dt className="text-muted">Intento GPU antes del respaldo</dt>
                            <dd>{ms(perception.primary_attempt_ms)}</dd>
                        </>
                    )}
                </dl>
            )}
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
