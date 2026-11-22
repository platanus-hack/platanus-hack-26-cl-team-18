import { cn } from "@/lib/cn";
import { moneyCl } from "@/lib/format";
import { useState, type ReactNode } from "react";
import type { ScreenView } from "./console-state";
import { ms } from "./labels";
import { percentile } from "./metrics";
import { pct } from "./panels";

/** A console pane: one mono label, an optional aside, the content. */
export function Pane({
    label,
    aside,
    children,
    className,
    ariaLabel,
}: {
    label: string;
    aside?: ReactNode;
    children: ReactNode;
    className?: string;
    ariaLabel?: string;
}) {
    return (
        <section
            aria-label={ariaLabel ?? label}
            className={cn(
                "flex min-w-0 flex-col gap-3 rounded-container border border-border bg-surface p-4",
                className,
            )}
        >
            <div className="flex items-center justify-between gap-3">
                <span className="eyebrow">{label}</span>
                {aside && (
                    <span className="font-mono text-metadata text-muted tabular-nums">{aside}</span>
                )}
            </div>
            {children}
        </section>
    );
}

const perSecond = (amount: string) => `${moneyCl(amount)}/s`;

/** The basket's value over the last auctions, newest last (presentation only). */
function useValueHistory(view: ScreenView, size = 32): { top: number[]; second: number[] } {
    const [history, setHistory] = useState<{ id: string; top: number; second: number }[]>([]);
    const auction = view.auction;
    // Derived during render (React's "adjust state when a prop changes"): one point per new auction.
    if (auction && auction.winners.length > 0 && history.at(-1)?.id !== auction.auction_id) {
        const top = Number(auction.bundle_value.amount);
        const second = Number(
            auction.alternatives[1]?.value.amount ?? auction.single_brand_value.amount,
        );
        setHistory([...history, { id: auction.auction_id, top, second }].slice(-size));
    }
    return { top: history.map((p) => p.top), second: history.map((p) => p.second) };
}

function Sparkline({ top, second }: { top: number[]; second: number[] }) {
    if (top.length < 2) return null;
    const lo = Math.min(...top, ...second) * 0.96;
    const hi = Math.max(...top, ...second) * 1.04 || 1;
    const x = (i: number) => (i / (top.length - 1)) * 100;
    const y = (v: number) => 40 - ((v - lo) / (hi - lo || 1)) * 40;
    const line = top
        .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(2)} ${y(v).toFixed(2)}`)
        .join(" ");
    const base = y(second.at(-1) ?? lo).toFixed(2);
    return (
        <svg
            viewBox="0 0 100 40"
            preserveAspectRatio="none"
            className="h-16 w-full overflow-visible"
            aria-hidden
        >
            <defs>
                <linearGradient id="winner-fade" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.22" />
                    <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0" />
                </linearGradient>
            </defs>
            <line
                x1="0"
                x2="100"
                y1={base}
                y2={base}
                stroke="var(--color-border-strong)"
                strokeDasharray="2 3"
                vectorEffect="non-scaling-stroke"
            />
            <path d={`${line} L100 40 L0 40 Z`} fill="url(#winner-fade)" />
            <path
                d={line}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth="1.75"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
            />
        </svg>
    );
}

/** "Gana": who holds the screen and what the moment is worth, with its recent trend. */
export function WinnerPanel({ view }: { view: ScreenView }) {
    const auction = view.auction;
    const history = useValueHistory(view);
    const won = auction !== null && auction.winners.length > 0;
    const second = auction?.alternatives[1];
    return (
        <Pane label="Gana" ariaLabel="Ganador">
            {won ? (
                <div className="flex flex-1 flex-col gap-3">
                    <div className="flex flex-col gap-1">
                        <p className="text-section font-semibold tracking-tight">
                            {auction.winners.map((w) => w.brand_name).join(" + ")}
                        </p>
                        {auction.offer && (
                            <p className="text-compact text-muted">
                                {auction.offer.label} · {moneyCl(auction.offer.price.amount)}
                            </p>
                        )}
                    </div>
                    <Sparkline top={history.top} second={history.second} />
                    <div className="mt-auto flex items-baseline justify-between gap-2">
                        <span className="text-kpi font-semibold tracking-tight tabular-nums">
                            {moneyCl(auction.bundle_value.amount)}
                            <span className="ml-0.5 text-compact font-medium text-muted">/s</span>
                        </span>
                        {second && (
                            <span className="font-mono text-metadata text-muted tabular-nums">
                                2.º {perSecond(second.value.amount)}
                            </span>
                        )}
                    </div>
                </div>
            ) : (
                <p className="flex flex-1 items-center text-compact text-muted">
                    {auction ? "Aviso de la casa" : "Nadie mira"}
                </p>
            )}
        </Pane>
    );
}

/** "Subasta": every basket the auction weighed, as bars that ease to each new result. */
export function AuctionRace({ view }: { view: ScreenView }) {
    const auction = view.auction;
    // Auctions seen this session, counted once per id (derived during render).
    const [count, setCount] = useState<{ last: string | null; n: number }>({ last: null, n: 0 });
    if (auction && auction.auction_id !== count.last) {
        setCount({ last: auction.auction_id, n: count.n + 1 });
    }
    const options = auction?.alternatives ?? [];
    const max = Math.max(...options.map((o) => Number(o.value.amount)), 1);
    const winner = auction && auction.winners.length > 0 ? options[0] : undefined;
    return (
        <Pane
            label="Subasta"
            ariaLabel="Subasta en vivo"
            aside={
                <span className="inline-flex items-center gap-2">
                    {/* Re-keyed per auction, so each new result replays the beat. */}
                    <span
                        key={count.last ?? "idle"}
                        aria-hidden
                        className="size-1.5 rounded-full bg-border-strong motion-safe:animate-[auction-beat_420ms_ease-out]"
                    />
                    {count.n > 0 && `#${count.n.toLocaleString("es-CL")}`}
                </span>
            }
        >
            {options.length === 0 ? (
                <p className="text-compact text-muted">Sin subasta mientras nadie mira.</p>
            ) : (
                <ol className="flex flex-col">
                    {options.map((option) => {
                        const name = option.brands.join(" + ");
                        const value = Number(option.value.amount);
                        const isWin = option === winner;
                        return (
                            <li
                                key={name}
                                className="grid grid-cols-[minmax(0,14rem)_minmax(0,1fr)_5.5rem] items-center gap-4 border-b border-border-subtle py-2.5 last:border-b-0 max-sm:grid-cols-[minmax(0,1fr)_5.5rem] max-sm:gap-y-1.5"
                            >
                                <span
                                    className={cn(
                                        "truncate text-compact",
                                        isWin && "font-semibold",
                                    )}
                                >
                                    {name}
                                </span>
                                <span className="relative h-1.5 overflow-hidden rounded-full bg-surface-sunken max-sm:col-span-2 max-sm:row-start-2">
                                    <span
                                        className={cn(
                                            "absolute inset-y-0 left-0 rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none",
                                            isWin ? "bg-accent" : "bg-border-strong",
                                        )}
                                        style={{ width: `${((value / max) * 100).toFixed(1)}%` }}
                                    />
                                </span>
                                <span
                                    className={cn(
                                        "text-right font-mono text-compact tabular-nums",
                                        isWin && "text-accent",
                                    )}
                                >
                                    {perSecond(option.value.amount)}
                                </span>
                            </li>
                        );
                    })}
                </ol>
            )}
        </Pane>
    );
}

/** Three numbers: who the ad reached, what was billed, how fast the screen reacted. */
export function Meters({ view }: { view: ScreenView }) {
    const p50 = percentile(view.glassToGlassMs, 50);
    const items = [
        { label: "Alcance", value: view.auction ? pct(view.auction.coverage) : "—", accent: true },
        { label: "Cobrado", value: moneyCl(String(view.billed)), accent: false },
        { label: "Reacción", value: p50 === null ? "—" : ms(p50), accent: false },
    ];
    return (
        <section
            aria-label="Sesión"
            className="grid grid-cols-3 overflow-hidden rounded-container border border-border bg-surface"
        >
            {items.map((item, index) => (
                <div
                    key={item.label}
                    className={cn(
                        "flex min-w-0 flex-col gap-1.5 p-4",
                        index > 0 && "border-l border-border",
                    )}
                >
                    <span className="eyebrow">{item.label}</span>
                    <span
                        className={cn(
                            "truncate text-title font-semibold tracking-tight tabular-nums sm:text-kpi",
                            item.accent && "text-accent",
                        )}
                    >
                        {item.value}
                    </span>
                </div>
            ))}
        </section>
    );
}
