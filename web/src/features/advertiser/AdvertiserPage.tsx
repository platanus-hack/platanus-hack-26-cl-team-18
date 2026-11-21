import type { BookLine } from "@/contract";
import { setBidMultiplier, useBook } from "@/lib/market-api";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { WorkspaceHeader } from "@/shared/ui/WorkspaceHeader";
import { useEffect, useRef, useState } from "react";
import { BidQueue, type BidState } from "./bid-queue";

const MIN = 0.2;
const MAX = 5;

/** `/advertiser?brand=Rayo`: a judge's phone becomes one brand's bid slider (beat 4). */
export function AdvertiserPage() {
    const { book, error, refresh } = useBook();
    const [brand, setBrand] = useState<string | null>(() =>
        new URLSearchParams(window.location.search).get("brand"),
    );
    const line = book?.lines.find((l) => l.brand_name === brand) ?? null;
    return (
        <div className="paper-theme">
            <WorkspaceHeader />
            <main className="operator-page flex flex-col gap-6 text-body">
                <header className="border-b border-rule pb-6">
                    <p className="eyebrow mb-3">Mercado en vivo</p>
                    <h1 className="page-heading">Bidding war</h1>
                    <p className="mt-3 text-muted">Tu marca, en el momento indicado.</p>
                </header>
                {error && <StatusBadge tone="danger" label="Can't reach the market" />}
                {!line ? (
                    <BrandPicker lines={book?.lines ?? []} onPick={setBrand} />
                ) : (
                    <Slider
                        key={line.campaign_id}
                        line={line}
                        rivals={book?.lines ?? []}
                        onSent={refresh}
                    />
                )}
            </main>
        </div>
    );
}

function BrandPicker({
    lines,
    onPick,
}: {
    lines: readonly BookLine[];
    onPick: (b: string) => void;
}) {
    return (
        <div className="flex flex-col gap-3">
            <p className="text-muted">Elige la marca por la que quieres pujar.</p>
            {lines.length === 0 && (
                <p className="tool-card text-muted" role="status">
                    Esperando marcas del mercado. Aparecerán aquí cuando estén disponibles.
                </p>
            )}
            {lines.map((l) => (
                <button
                    key={l.campaign_id}
                    type="button"
                    className="rounded-container border border-border bg-surface px-5 py-5 text-left font-medium hover:bg-accent-soft"
                    style={{ borderLeft: `6px solid ${l.accent_color ?? "transparent"}` }}
                    onClick={() => {
                        onPick(l.brand_name);
                    }}
                >
                    {l.brand_name} <span className="text-muted">· {l.category}</span>
                </button>
            ))}
        </div>
    );
}

function Slider({
    line,
    rivals,
    onSent,
}: {
    line: BookLine;
    rivals: readonly BookLine[];
    onSent: () => void;
}) {
    // While dragging, show the draft; otherwise follow the server (another phone may move it).
    const [bid, setBid] = useState<BidState>({ kind: "idle", value: null });
    const queue = useRef<BidQueue | null>(null);
    const [confirmedSeq, setConfirmedSeq] = useState(0);
    const refresh = useRef(onSent);
    const observedSeq = useRef(line.multiplier_seq);
    useEffect(() => {
        observedSeq.current = line.multiplier_seq;
    }, [line.multiplier_seq]);
    useEffect(() => {
        refresh.current = onSent;
    }, [onSent]);
    useEffect(() => {
        const current = new BidQueue(
            async (next) => {
                setConfirmedSeq(
                    await setBidMultiplier(line.campaign_id, next.toFixed(1), observedSeq.current),
                );
            },
            setBid,
            () => {
                refresh.current();
            },
        );
        queue.current = current;
        return () => {
            current.dispose();
            queue.current = null;
        };
    }, [line.campaign_id]);
    const value =
        bid.kind === "idle" && line.multiplier_seq >= confirmedSeq
            ? Number(line.multiplier)
            : (bid.value ?? Number(line.multiplier));
    const competitors = rivals.filter(
        (r) => r.category === line.category && r.campaign_id !== line.campaign_id,
    );
    const send = (next: number) => {
        queue.current?.set(next);
    };
    return (
        <section className="tool-card flex flex-col gap-6">
            <div className="flex items-center justify-between">
                <span className="text-section font-semibold">{line.brand_name}</span>
                {line.on_air ? (
                    <StatusBadge tone="success" label="En pantalla" />
                ) : (
                    <StatusBadge tone="neutral" label="Fuera de pantalla" />
                )}
            </div>
            <label className="flex flex-col gap-2">
                <span className="text-muted">Multiplicador de puja</span>
                <span className="text-kpi font-semibold tabular-nums">×{value.toFixed(1)}</span>
                <input
                    type="range"
                    min={MIN}
                    max={MAX}
                    step={0.1}
                    value={value}
                    aria-label="Bid multiplier"
                    className="h-10 w-full"
                    onChange={(e) => {
                        send(Number(e.target.value));
                    }}
                />
            </label>
            <div role="status" aria-live="polite">
                {bid.kind === "sending" && (
                    <StatusBadge tone="neutral" label="Enviando última puja…" />
                )}
                {bid.kind === "idle" && (
                    <span className="text-metadata text-muted">
                        Puja confirmada ×{value.toFixed(1)}
                    </span>
                )}
                {bid.kind === "failed" && (
                    <button
                        type="button"
                        className="button-secondary"
                        onClick={() => {
                            send(bid.value);
                        }}
                    >
                        No enviada · Reintentar ×{bid.value.toFixed(1)}
                    </button>
                )}
            </div>
            <button
                type="button"
                className="button-secondary"
                onClick={() => {
                    send(1);
                }}
            >
                Restablecer ×1.0
            </button>
            <p className="text-metadata text-muted tabular-nums">
                Saldo {line.remaining.amount} {line.remaining.currency} · gastado{" "}
                {line.spent.amount}
            </p>
            {competitors.length > 0 && (
                <ul className="flex flex-col gap-1 text-metadata">
                    {competitors.map((r) => (
                        <li key={r.campaign_id} className="flex justify-between">
                            <span>Rival {r.brand_name}</span>
                            <span className="tabular-nums">
                                ×{r.multiplier} {r.on_air ? "· en pantalla" : ""}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            <p className="text-metadata text-muted">
                Una puja mayor compite por el momento actual; no garantiza ganar. La relevancia,
                seguridad, stock y saldo siguen aplicando. El cambio espera el tiempo mínimo del
                anuncio.
            </p>
        </section>
    );
}
