import type { DevBox } from "@/contract";
import { CreativeView } from "@/features/screen";
import { demoKey, rememberedDemoKey } from "@/lib/demo";
import { decimalCl, moneyCl } from "@/lib/format";
import { setBidMultiplier, setStock } from "@/lib/market-api";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Camera, Minus, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { bidRows, splitRows, stepMultiplier, type BidRow } from "./bids";
import type { ScreenView } from "./console-state";
import { categoryLabel, money, ms, SOURCE } from "./labels";
import { serverDecisionMs } from "./metrics";
import { DEMO_BRANDS } from "./panels";
import { baskets, explain } from "./why";

/** Detections older than this are not "what the camera sees" anymore. */
const PERCEPTION_FRESH_MS = 3000;

/** Bid and stock controls need the demo key; the camera boxes don't. */
const hasDemoKey = () => (demoKey() ?? rememberedDemoKey()) !== null;

function useNow(intervalMs: number): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        const timer = setInterval(() => {
            setNow(Date.now());
        }, intervalMs);
        return () => {
            clearInterval(timer);
        };
    }, [intervalMs]);
    return now;
}

function Section({
    title,
    aside,
    children,
    label,
}: {
    title: string;
    aside?: string | null | undefined;
    children: React.ReactNode;
    label?: string;
}) {
    return (
        <section aria-label={label ?? title} className="flex min-w-0 flex-col gap-2.5">
            <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-section font-semibold">{title}</h2>
                {aside && <span className="text-metadata text-muted">{aside}</span>}
            </div>
            {children}
        </section>
    );
}

const pct = (score: number) => decimalCl(String(score), 2);

function boxStyle(box: DevBox): React.CSSProperties {
    return {
        left: `${box.x1 * 100}%`,
        top: `${box.y1 * 100}%`,
        width: `${(box.x2 - box.x1) * 100}%`,
        height: `${(box.y2 - box.y1) * 100}%`,
    };
}

/** "Lo que ve la cámara": the detector's boxes, never an image (nothing is stored). */
export function CameraPanel({ view }: { view: ScreenView }) {
    const now = useNow(1000);
    const fresh =
        view.perception && now - view.perception.at < PERCEPTION_FRESH_MS
            ? view.perception.message
            : null;
    const looking = fresh?.people.filter((p) => p.looking).length ?? view.moment?.looking ?? 0;
    const people = fresh?.people.length ?? view.moment?.people ?? 0;
    return (
        <Section
            title="Lo que ve la cámara"
            aside={fresh && `${fresh.detector} · ${Math.round(fresh.inference_ms)} ms`}
        >
            <div
                className={
                    fresh
                        ? "relative overflow-hidden rounded-container bg-ink"
                        : "relative overflow-hidden rounded-container border border-dashed border-border-strong bg-surface-raised"
                }
                style={{
                    aspectRatio: fresh ? `${fresh.frame_w} / ${fresh.frame_h}` : "16 / 9",
                }}
                aria-label="Detecciones de la cámara"
            >
                {fresh ? (
                    <>
                        {fresh.preview_jpeg_base64 && (
                            // Demo only: the same frame, faces blurred, never stored.
                            <img
                                src={`data:image/jpeg;base64,${fresh.preview_jpeg_base64}`}
                                alt=""
                                className="absolute inset-0 h-full w-full object-cover"
                            />
                        )}
                        {fresh.people.map((person, index) => (
                            <div
                                key={person.track_id}
                                className="absolute rounded-sm border-2 border-(--palette-verde)"
                                style={boxStyle(person.box)}
                            >
                                <span className="absolute -top-5 left-[-2px] rounded-sm bg-(--palette-verde) px-1.5 text-metadata font-semibold whitespace-nowrap text-ink">
                                    persona {index + 1} · {person.looking ? "mirando" : "no mira"} ·{" "}
                                    {pct(person.confidence)}
                                </span>
                            </div>
                        ))}
                        {fresh.objects.map((object, index) => (
                            <div
                                key={`${object.label}:${index}`}
                                className="absolute rounded-sm border-2 border-(--palette-lila)"
                                style={boxStyle(object.box)}
                            >
                                <span className="absolute -bottom-5 left-[-2px] rounded-sm bg-(--palette-lila) px-1.5 text-metadata font-semibold whitespace-nowrap text-ink">
                                    {object.label} {pct(object.confidence)}
                                </span>
                            </div>
                        ))}
                        {fresh.markers.map((marker) => (
                            <div
                                key={marker.marker_id}
                                className="absolute rounded-sm border-2 border-(--palette-naranjo)"
                                style={boxStyle(marker.box)}
                            >
                                <span className="absolute -bottom-5 left-[-2px] rounded-sm bg-(--palette-naranjo) px-1.5 text-metadata font-semibold whitespace-nowrap text-ink">
                                    {(marker.brand_ref && DEMO_BRANDS[marker.brand_ref]) ??
                                        marker.brand_ref ??
                                        "producto"}{" "}
                                    · marcador #{marker.marker_id}
                                </span>
                            </div>
                        ))}
                        <span className="absolute top-3 right-3 rounded-full bg-ink/80 px-2.5 py-1 text-metadata text-white">
                            {fresh.preview_jpeg_base64
                                ? "Vista demo · rostros difuminados · nada se guarda"
                                : "Solo cajas · nada se guarda"}
                        </span>
                    </>
                ) : (
                    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
                        <Camera className="size-6 text-muted" aria-hidden />
                        <p className="text-body font-medium">
                            {people > 0
                                ? `${people} ${people === 1 ? "persona" : "personas"} · ${looking} mirando`
                                : "Sin detecciones"}
                        </p>
                        <p className="max-w-xs text-compact text-muted">
                            {people > 0
                                ? "La vista de cajas aparece cuando el agente de cámara envía sus detecciones."
                                : "Las cajas aparecen cuando la cámara está en vivo y ve a alguien."}
                        </p>
                    </div>
                )}
            </div>
            {fresh && (
                <p className="text-compact text-muted">
                    {people} {people === 1 ? "persona" : "personas"} · {looking} mirando
                </p>
            )}
        </Section>
    );
}

/** "Lo que ve la gente": the ad on air and, while a swap waits, the next one. */
export function PeopleScreen({ view }: { view: ScreenView }) {
    const now = useNow(250);
    const onAir = view.onAir;
    const next = view.next;
    const waiting = next ? new Date(next.not_before).getTime() - now : null;
    const showNext = next !== null && waiting !== null && waiting > -2000;
    return (
        <Section
            title="Lo que ve la gente"
            aside={onAir && (onAir.auction_id === null ? "aviso de la casa" : "aviso pagado")}
        >
            <div
                className="relative aspect-video overflow-hidden rounded-container border border-border bg-ink shadow-resting"
                aria-label="Aviso al aire"
            >
                {onAir ? (
                    <CreativeView spec={onAir.spec} playKey={onAir.creative_id} />
                ) : (
                    <p className="grid h-full place-items-center text-compact text-white/75">
                        Esperando la pantalla…
                    </p>
                )}
            </div>
            {showNext ? (
                <Card className="flex items-center gap-4 p-3" aria-label="Siguiente aviso">
                    <div className="relative aspect-video w-48 shrink-0 overflow-hidden rounded-control border border-border bg-ink">
                        <CreativeView spec={next.spec} playKey={`next:${next.not_before}`} />
                    </div>
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="font-mono text-metadata tracking-wider text-accent uppercase">
                            Siguiente ·{" "}
                            {waiting > 0
                                ? `entra en ${decimalCl(String(waiting / 1000), 1)} s`
                                : "entrando"}
                        </span>
                        <span className="font-semibold">
                            {next.auction_id === null
                                ? "Aviso de la casa"
                                : next.spec.slots.map((s) => s.brand_name).join(" + ")}
                            {next.spec.offer &&
                                ` · ${next.spec.offer.label} ${moneyCl(next.spec.offer.price.amount)}`}
                        </span>
                    </div>
                </Card>
            ) : (
                onAir?.spec.offer && (
                    <p className="text-compact text-muted">
                        {`${onAir.spec.slots.map((s) => s.brand_name).join(" + ")} · ${onAir.spec.offer.label} ${moneyCl(onAir.spec.offer.price.amount)}`}
                    </p>
                )
            )}
        </Section>
    );
}

/** "La subasta": every bid and why, the baskets weighed, demo controls with the key. */
export function AuctionSection({ view, screen }: { view: ScreenView; screen: string }) {
    const auction = view.auction;
    const controls = hasDemoKey();
    const [error, setError] = useState<string | null>(null);
    if (!view.book) {
        return (
            <Card className="p-5">
                <p className="text-compact text-muted">Cargando marcas…</p>
            </Card>
        );
    }
    const { shown, idle } = splitRows(bidRows(view.book, auction));
    const lines = baskets(auction);
    const live = auction !== null;
    return (
        <Card className="flex flex-col gap-4 p-5" aria-label="La subasta">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div className="flex items-baseline gap-3">
                    <h2 className="text-section font-semibold">La subasta</h2>
                    {!auction && (
                        <span className="text-compact text-muted">
                            {shown.length} marcas listas · la subasta empieza cuando alguien mire
                        </span>
                    )}
                    {auction && (
                        <span className="text-compact text-muted tabular-nums">
                            {`${auction.bids.length} pujas · ${new Date(auction.trace.cleared_at ?? auction.trace.measured_at ?? 0).toLocaleTimeString("es-CL", { hour12: false })}`}
                        </span>
                    )}
                </div>
                {auction && auction.winners.length > 0 && (
                    <span className="text-compact tabular-nums">
                        Canasta{" "}
                        <strong className="font-semibold">{money(auction.bundle_value)}/s</strong>{" "}
                        <span className="text-muted">
                            vs. mejor marca sola {money(auction.single_brand_value)}/s
                        </span>
                    </span>
                )}
            </div>
            <div
                className={
                    lines.length > 0
                        ? "grid gap-6 xl:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)]"
                        : "grid gap-6"
                }
            >
                {!live ? (
                    <BrandGrid
                        rows={shown}
                        screen={screen}
                        controls={controls}
                        onError={setError}
                        error={error}
                    />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[720px] border-collapse text-compact">
                            <thead>
                                <tr className="text-left whitespace-nowrap text-muted">
                                    <th className="border-b border-border py-2 pr-2 pl-3 font-medium">
                                        Marca
                                    </th>
                                    <th className="border-b border-border p-2 text-right font-medium">
                                        Puja/s
                                    </th>
                                    <th className="border-b border-border p-2 text-center font-medium">
                                        Multiplicador
                                    </th>
                                    <>
                                        <th className="border-b border-border p-2 font-medium">
                                            Coincide con
                                        </th>
                                        <th className="border-b border-border p-2 text-right font-medium">
                                            Valor/s
                                        </th>
                                        <th
                                            className="border-b border-border p-2 text-right font-medium"
                                            title="Cada ganadora paga lo justo para seguir ganando, nunca más que su puja."
                                        >
                                            Paga/s
                                        </th>
                                        <th className="border-b border-border py-2 pl-2 font-medium">
                                            Resultado
                                        </th>
                                    </>
                                </tr>
                            </thead>
                            <tbody>
                                {shown.map((row) => (
                                    <AuctionRow
                                        key={row.campaignId}
                                        row={row}
                                        screen={screen}
                                        controls={controls}
                                        live={live}
                                        onError={setError}
                                    />
                                ))}
                            </tbody>
                        </table>
                        {idle.length > 0 && (
                            <details className="mt-2 pl-3 text-compact text-muted">
                                <summary className="cursor-pointer">
                                    Sin interés en este momento ({idle.length})
                                </summary>
                                <p className="mt-1">{idle.map((row) => row.brand).join(", ")}</p>
                            </details>
                        )}
                        {error && (
                            <p className="mt-2 text-compact text-danger" role="status">
                                No se aplicó: {error}
                            </p>
                        )}
                        {!controls && (
                            <p className="mt-2 pl-3 text-metadata text-muted">
                                Solo lectura: abre una vez /demo/&lt;clave&gt; para editar.
                            </p>
                        )}
                    </div>
                )}
                {lines.length > 0 && (
                    <div className="flex flex-col gap-2.5">
                        <span className="font-mono text-metadata tracking-wider text-muted uppercase">
                            Canastas evaluadas
                        </span>
                        <ol className="flex flex-col gap-2" aria-label="Canastas evaluadas">
                            {lines.map((line) => (
                                <li
                                    key={line.name}
                                    className={
                                        line.tone === "win"
                                            ? "flex flex-col gap-0.5 rounded-control border border-accent bg-accent-soft px-3 py-2"
                                            : "flex flex-col gap-0.5 rounded-control border border-border px-3 py-2"
                                    }
                                >
                                    <span className="flex justify-between gap-2 text-compact">
                                        <span className="font-semibold">{line.name}</span>
                                        <span
                                            className={
                                                line.tone === "blocked"
                                                    ? "text-warning"
                                                    : line.tone === "other"
                                                      ? "text-muted tabular-nums"
                                                      : "tabular-nums"
                                            }
                                        >
                                            {line.value}
                                        </span>
                                    </span>
                                    <span className="text-metadata text-muted">{line.note}</span>
                                </li>
                            ))}
                        </ol>
                    </div>
                )}
            </div>
        </Card>
    );
}

/** Standby: every brand as a compact tile, ready to bid, before anyone is seen. */
function BrandGrid({
    rows,
    screen,
    controls,
    onError,
    error,
}: {
    rows: readonly BidRow[];
    screen: string;
    controls: boolean;
    onError: (message: string | null) => void;
    error: string | null;
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
    return (
        <div className="flex flex-col gap-2">
            <ul aria-label="Marcas" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {rows.map((row) => (
                    <li
                        key={row.campaignId}
                        data-brand={row.brand}
                        className="flex items-center gap-3 rounded-control border border-border-subtle bg-surface-raised px-3 py-2"
                    >
                        <span
                            aria-hidden
                            className="size-2.5 shrink-0 rounded-full"
                            style={{ background: row.accent ?? "var(--color-border)" }}
                        />
                        <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate font-medium">{row.brand}</span>
                            <span className="truncate text-metadata text-muted">
                                {categoryLabel(row.category)} · {money(row.remaining)}
                            </span>
                        </span>
                        {controls ? (
                            <>
                                <button
                                    type="button"
                                    role="switch"
                                    aria-checked={row.inStock}
                                    aria-label={`Stock de ${row.brand}`}
                                    title={row.inStock ? "Con stock" : "Sin stock"}
                                    className={
                                        row.inStock
                                            ? "rounded-control border border-control-edge px-1.5 text-metadata whitespace-nowrap"
                                            : "rounded-control border border-(--state-danger-edge) px-1.5 text-metadata whitespace-nowrap text-danger"
                                    }
                                    onClick={() => {
                                        run(setStock(screen, row.brandId, !row.inStock));
                                    }}
                                >
                                    {row.inStock ? "Stock" : "Sin stock"}
                                </button>
                                <span className="inline-flex items-center rounded-control border border-border bg-surface tabular-nums">
                                    <Button
                                        size="icon-sm"
                                        variant="ghost"
                                        aria-label={`Bajar puja de ${row.brand}`}
                                        disabled={stepMultiplier(row.multiplier, -1) === null}
                                        onClick={() => {
                                            const next = stepMultiplier(row.multiplier, -1);
                                            if (next !== null)
                                                run(
                                                    setBidMultiplier(
                                                        row.campaignId,
                                                        next,
                                                        row.multiplierSeq,
                                                    ),
                                                );
                                        }}
                                    >
                                        <Minus aria-hidden />
                                    </Button>
                                    <span className="min-w-9 text-center text-compact">
                                        ×{decimalCl(row.multiplier, 1)}
                                    </span>
                                    <Button
                                        size="icon-sm"
                                        variant="ghost"
                                        aria-label={`Subir puja de ${row.brand}`}
                                        disabled={stepMultiplier(row.multiplier, 1) === null}
                                        onClick={() => {
                                            const next = stepMultiplier(row.multiplier, 1);
                                            if (next !== null)
                                                run(
                                                    setBidMultiplier(
                                                        row.campaignId,
                                                        next,
                                                        row.multiplierSeq,
                                                    ),
                                                );
                                        }}
                                    >
                                        <Plus aria-hidden />
                                    </Button>
                                </span>
                            </>
                        ) : (
                            <span className="text-compact text-muted tabular-nums">
                                ×{decimalCl(row.multiplier, 1)}
                                {!row.inStock && (
                                    <span className="ml-2 text-danger">Sin stock</span>
                                )}
                            </span>
                        )}
                    </li>
                ))}
            </ul>
            {error && (
                <p className="text-compact text-danger" role="status">
                    No se aplicó: {error}
                </p>
            )}
            {!controls && (
                <p className="text-metadata text-muted">
                    Solo lectura: abre una vez /demo/&lt;clave&gt; para editar.
                </p>
            )}
        </div>
    );
}

function AuctionRow({
    row,
    screen,
    controls,
    live,
    onError,
}: {
    row: BidRow;
    screen: string;
    controls: boolean;
    live: boolean;
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
    const won = row.result?.label === "Gana";
    return (
        <tr
            data-brand={row.brand}
            className={
                won ? "bg-accent-soft shadow-[inset_2px_0_0_var(--color-accent)]" : undefined
            }
        >
            <td className="border-b border-border-subtle py-2 pr-2 pl-3">
                <span className="flex flex-col gap-0.5">
                    <span className="flex items-center gap-2 font-medium whitespace-nowrap">
                        <span
                            aria-hidden
                            className="size-2.5 shrink-0 rounded-full"
                            style={{ background: row.accent ?? "var(--color-border)" }}
                        />
                        {row.brand}
                        {row.onAir && <span className="text-metadata text-success">· al aire</span>}
                    </span>
                    <span className="flex items-center gap-2 pl-4.5 text-metadata whitespace-nowrap text-muted">
                        {categoryLabel(row.category)} · {money(row.remaining)}
                        {controls ? (
                            <button
                                type="button"
                                role="switch"
                                aria-checked={row.inStock}
                                aria-label={`Stock de ${row.brand}`}
                                className={
                                    row.inStock
                                        ? "rounded-control border border-control-edge px-1.5 whitespace-nowrap"
                                        : "rounded-control border border-(--state-danger-edge) px-1.5 whitespace-nowrap text-danger"
                                }
                                onClick={() => {
                                    run(setStock(screen, row.brandId, !row.inStock));
                                }}
                            >
                                {row.inStock ? "Con stock" : "Sin stock"}
                            </button>
                        ) : (
                            !row.inStock && <span className="text-danger">Sin stock</span>
                        )}
                    </span>
                </span>
            </td>
            {live && (
                <td className="border-b border-border-subtle p-2 text-right tabular-nums">
                    {row.bidPerAs ? money(row.bidPerAs) : "—"}
                </td>
            )}
            <td
                className={
                    live
                        ? "border-b border-border-subtle p-2 text-center"
                        : "border-b border-border-subtle p-2 text-right"
                }
            >
                <span className="inline-flex items-center rounded-control border border-border bg-surface tabular-nums">
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
                    <span className="min-w-10 px-1 text-center">
                        ×{decimalCl(row.multiplier, 1)}
                    </span>
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
                </span>
                {row.autobid && (
                    <Badge tone="info" className="ml-1">
                        auto
                    </Badge>
                )}
            </td>
            {live && (
                <>
                    <td className="border-b border-border-subtle p-2">
                        {row.match ? (
                            <span className="flex flex-col gap-0.5">
                                <span>{row.match}</span>
                                <span className="text-metadata text-muted">{row.matchKind}</span>
                            </span>
                        ) : (
                            <span className="text-muted">—</span>
                        )}
                    </td>
                    <td className="border-b border-border-subtle p-2 text-right">
                        {row.valuePerS ? (
                            <span className="inline-flex flex-col items-end gap-1 font-medium tabular-nums">
                                {money(row.valuePerS)}
                                <span
                                    aria-hidden
                                    className="block h-1 w-24 rounded-full bg-border-subtle"
                                >
                                    <span
                                        className={
                                            won
                                                ? "block h-1 rounded-full bg-accent"
                                                : "block h-1 rounded-full bg-(--palette-gris-oscuro)"
                                        }
                                        style={{ width: `${Math.round(row.valueShare * 100)}%` }}
                                    />
                                </span>
                            </span>
                        ) : (
                            <span className="text-muted">—</span>
                        )}
                    </td>
                    <td className="border-b border-border-subtle p-2 text-right font-semibold text-success tabular-nums">
                        {row.pays ? (
                            money(row.pays)
                        ) : (
                            <span className="font-normal text-muted">—</span>
                        )}
                    </td>
                    <td className="border-b border-border-subtle py-2 pl-2">
                        {row.result ? (
                            <span className="flex flex-col gap-0.5">
                                <Badge tone={row.result.tone}>{row.result.label}</Badge>
                                {row.reason && (
                                    <span className="text-metadata text-muted">{row.reason}</span>
                                )}
                            </span>
                        ) : (
                            <span className="text-metadata text-muted">—</span>
                        )}
                    </td>
                </>
            )}
        </tr>
    );
}

/** "Etiquetas": what the system understood, each with its source and confidence. */
export function TagsPanel({ view }: { view: ScreenView }) {
    const moment = view.moment;
    const segments = moment ? [...moment.segments].sort((a, b) => b.score - a.score) : [];
    return (
        <Card className="flex flex-col gap-3.5 p-5" aria-label="Etiquetas">
            <div className="flex items-baseline justify-between">
                <h2 className="text-section font-semibold">Etiquetas</h2>
                <span className="text-metadata text-muted">IAB</span>
            </div>
            {segments.length === 0 ? (
                <p className="text-compact text-muted">Sin intereses claros.</p>
            ) : (
                segments.map((segment) => (
                    <div key={segment.segment_id} className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-2 text-body">
                            <span className="font-medium">{segment.label}</span>
                            {segment.sources.map((source) => (
                                <span
                                    key={source}
                                    className="rounded-sm bg-(--state-info-fill) px-1.5 py-0.5 font-mono text-micro tracking-wider text-(--state-info-ink) uppercase"
                                >
                                    {SOURCE[source]}
                                </span>
                            ))}
                            <span className="ml-auto text-compact text-muted tabular-nums">
                                {Math.round(segment.score * 100)} %
                            </span>
                        </div>
                        <span aria-hidden className="block h-0.5 rounded-full bg-border-subtle">
                            <span
                                className="block h-full rounded-full bg-accent"
                                style={{ width: `${Math.round(segment.score * 100)}%` }}
                            />
                        </span>
                    </div>
                ))
            )}
            {moment && (moment.held_products.length > 0 || moment.minor_likely.value) && (
                <div className="flex flex-wrap gap-2 border-t border-border-subtle pt-3 text-metadata">
                    {moment.held_products.map((product) => (
                        <span
                            key={`${product.brand_ref ?? ""}:${product.category}`}
                            className="rounded-full border border-(--palette-naranjo) px-2.5 py-0.5 text-warning"
                        >
                            En mano:{" "}
                            {(product.brand_ref && DEMO_BRANDS[product.brand_ref]) ??
                                product.brand_ref ??
                                product.category}
                        </span>
                    ))}
                    {moment.minor_likely.value && (
                        <span className="rounded-full border border-border-strong px-2.5 py-0.5">
                            Posible menor: categorías restringidas bloqueadas
                        </span>
                    )}
                </div>
            )}
        </Card>
    );
}

/** "Por qué este aviso": one sentence, four short steps, one line for what was left out. */
export function WhyPanel({ view }: { view: ScreenView }) {
    const decided =
        view.next && view.next.auction_id === view.auction?.auction_id ? view.next : view.onAir;
    const why = explain(view.moment, view.auction, decided);
    const decision = view.lastTrace ? serverDecisionMs(view.lastTrace) : null;
    return (
        <Card className="flex flex-col gap-3.5 p-5" aria-label="Por qué este aviso">
            <div className="flex items-baseline justify-between">
                <h2 className="text-section font-semibold">Por qué este aviso</h2>
                {decision !== null && (
                    <span className="text-metadata text-muted">decidido en {ms(decision)}</span>
                )}
            </div>
            {!why ? (
                <p className="text-compact text-muted">Sin decisión todavía.</p>
            ) : (
                <>
                    <p className="text-title leading-snug font-medium tracking-tight">
                        {why.headline}
                    </p>
                    <ol aria-label="Cómo se decidió" className="grid gap-2 sm:grid-cols-4">
                        {why.steps.map((step, index) => (
                            <li
                                key={step.step}
                                className={
                                    index === 2
                                        ? "flex flex-col gap-1 rounded-control border border-accent bg-accent-soft p-3"
                                        : index === 3
                                          ? "flex flex-col gap-1 rounded-control border border-(--state-success-edge) bg-(--state-success-fill) p-3"
                                          : "flex flex-col gap-1 rounded-control border border-border-subtle bg-surface-raised p-3"
                                }
                            >
                                <span className="font-mono text-micro tracking-wider text-muted uppercase">
                                    {index + 1} · {step.step}
                                </span>
                                <span className="text-body leading-snug font-semibold">
                                    {step.text}
                                </span>
                            </li>
                        ))}
                    </ol>
                    <p className="text-compact text-muted">{why.leftOut}</p>
                </>
            )}
        </Card>
    );
}
