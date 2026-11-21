import type { BookLine } from "@/contract";
import { categoryLabel } from "@/lib/categories";
import { decimalCl, moneyCl } from "@/lib/format";
import { setAutoBid, setBidMultiplier, useBook } from "@/lib/market-api";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { AppShell, EmptyState, PageHeader } from "@/shared/ui/page";
import { ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { BidQueue, type BidState } from "./bid-queue";

const MIN = 0.2;
const MAX = 5;

const category = categoryLabel;

const decimal = (value: number) => decimalCl(value.toFixed(1));

/** `/advertiser?brand=Rayo`: a judge's phone becomes one brand's bid slider (beat 4). */
export function AdvertiserPage() {
    const { book, error, refresh } = useBook();
    const [brand, setBrand] = useState<string | null>(() =>
        new URLSearchParams(window.location.search).get("brand"),
    );
    const line = book?.lines.find((l) => l.brand_name === brand) ?? null;
    return (
        <AppShell width="narrow">
            <PageHeader
                title={line ? line.brand_name : "Pujas"}
                description={line ? category(line.category) : "Elige la marca por la que pujas."}
                actions={
                    line && (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                                setBrand(null);
                            }}
                        >
                            Cambiar marca
                        </Button>
                    )
                }
            />
            {error && (
                <Badge tone="danger" appearance="soft">
                    Sin conexión con el mercado
                </Badge>
            )}
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
        </AppShell>
    );
}

function BrandPicker({
    lines,
    onPick,
}: {
    lines: readonly BookLine[];
    onPick: (b: string) => void;
}) {
    if (lines.length === 0) {
        return (
            <Card>
                <EmptyState
                    title="Sin marcas todavía"
                    description="Aparecen cuando el mercado publica su libro."
                />
            </Card>
        );
    }
    return (
        <Card className="divide-y divide-border-subtle overflow-hidden">
            {lines.map((l) => (
                <button
                    key={l.campaign_id}
                    type="button"
                    className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-surface-hover"
                    onClick={() => {
                        onPick(l.brand_name);
                    }}
                >
                    <span
                        aria-hidden
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ background: l.accent_color ?? "var(--palette-gris-oscuro)" }}
                    />
                    <span className="font-medium">{l.brand_name}</span>
                    <span className="text-compact text-muted">{category(l.category)}</span>
                    {l.on_air && (
                        <Badge tone="success" className="ml-auto">
                            Al aire
                        </Badge>
                    )}
                    <ChevronRight
                        aria-hidden
                        className={l.on_air ? "size-4 text-muted" : "ml-auto size-4 text-muted"}
                    />
                </button>
            ))}
        </Card>
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
    const [autoFailed, setAutoFailed] = useState(false);
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
        <Card className="flex flex-col gap-6 p-4">
            <div className="flex items-center justify-between">
                <label htmlFor="bid" className="text-compact text-muted">
                    Multiplicador de puja
                </label>
                {line.on_air ? (
                    <Badge tone="success" appearance="soft">
                        Al aire
                    </Badge>
                ) : (
                    <Badge appearance="soft">En espera</Badge>
                )}
            </div>
            <div className="flex flex-col gap-3">
                <output htmlFor="bid" className="text-kpi leading-none font-medium tabular-nums">
                    ×{decimal(value)}
                </output>
                <input
                    id="bid"
                    type="range"
                    min={MIN}
                    max={MAX}
                    step={0.1}
                    value={value}
                    className="h-10 w-full"
                    onChange={(e) => {
                        send(Number(e.target.value));
                    }}
                />
                <div className="flex justify-between text-metadata text-muted tabular-nums">
                    <span>×{decimal(MIN)}</span>
                    <span>×{decimal(MAX)}</span>
                </div>
            </div>
            <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-3">
                {bid.kind === "sending" && <Badge appearance="soft">Enviando última puja…</Badge>}
                {bid.kind === "idle" && bid.value !== null && (
                    <span className="text-metadata text-muted">
                        Puja confirmada ×{decimal(bid.value)}
                    </span>
                )}
                {bid.kind === "failed" && (
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                            send(bid.value);
                        }}
                    >
                        No enviada · Reintentar ×{decimal(bid.value)}
                    </Button>
                )}
                {autoFailed && (
                    <Badge tone="warning" appearance="soft">
                        No se pudo cambiar la puja automática.
                    </Badge>
                )}
            </div>
            <div className="flex flex-col gap-2">
                <Button
                    variant={line.autobid ? "primary" : "outline"}
                    aria-pressed={line.autobid}
                    onClick={() => {
                        setAutoFailed(false);
                        setAutoBid(line.campaign_id, !line.autobid)
                            .then(onSent)
                            .catch(() => {
                                setAutoFailed(true);
                            });
                    }}
                >
                    {line.autobid ? "Puja automática: activa" : "Activar puja automática"}
                </Button>
                {line.autobid && line.autobid_reason && (
                    <p className="text-metadata text-muted">{line.autobid_reason}</p>
                )}
            </div>
            <dl className="grid grid-cols-2 gap-4 border-t border-border-subtle pt-4 text-compact">
                <div>
                    <dt className="text-muted">Presupuesto disponible</dt>
                    <dd className="font-medium tabular-nums">{moneyCl(line.remaining.amount)}</dd>
                </div>
                <div>
                    <dt className="text-muted">Gastado</dt>
                    <dd className="font-medium tabular-nums">{moneyCl(line.spent.amount)}</dd>
                </div>
            </dl>
            {competitors.length > 0 && (
                <div className="flex flex-col gap-2 border-t border-border-subtle pt-4">
                    <p className="text-compact text-muted">Competidores</p>
                    <ul className="flex flex-col gap-1 text-compact">
                        {competitors.map((r) => (
                            <li key={r.campaign_id} className="flex items-center justify-between">
                                <span>{r.brand_name}</span>
                                <span className="flex items-center gap-3 tabular-nums">
                                    {r.on_air && <Badge tone="success">Al aire</Badge>}×
                                    {decimalCl(r.multiplier, 1)}
                                </span>
                            </li>
                        ))}
                    </ul>
                    <p className="text-metadata text-muted">
                        Los competidores nunca comparten canasta. El cambio espera a que termine el
                        aviso actual.
                    </p>
                </div>
            )}
        </Card>
    );
}
