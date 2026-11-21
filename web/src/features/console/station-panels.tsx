import { decimalCl, moneyCl } from "@/lib/format";
import { Badge } from "@/shared/ui/badge";
import { Metric } from "@/shared/ui/metric";
import { Panel } from "@/shared/ui/Panel";
import type { ScreenView } from "./console-state";
import { money, ms, signedPct } from "./labels";
import { coBrandLift, percentile, serverDecisionMs } from "./metrics";
import { pct } from "./panels";

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
    // Session total over every receipt, not just the shown window (presentation only).
    const billed = view.billed;
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
