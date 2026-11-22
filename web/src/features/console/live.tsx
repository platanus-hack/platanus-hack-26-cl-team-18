import { cn } from "@/lib/cn";
import { decimalCl, moneyCl } from "@/lib/format";
import type { ReactNode } from "react";
import { BrandLogo } from "./BrandLogo";
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

/** A per-second rate, always with two decimals so a list of them lines up ("$6,89/s"). */
const perSecond = (amount: string) => `$${decimalCl(amount, 2)}/s`;

/** A brand's real logo from the book, looked up by its name (nothing when the book hasn't arrived). */
function LogoFor({
    view,
    brand,
    size = "sm",
    highlight = false,
}: {
    view: ScreenView;
    brand: string;
    size?: "sm" | "md";
    highlight?: boolean;
}) {
    const line = view.book?.lines.find((l) => l.brand_name === brand);
    if (!line) return null;
    return (
        <BrandLogo
            src={line.logo_url}
            accent={line.accent_color ?? null}
            size={size}
            highlight={highlight}
        />
    );
}

/** "Gana": who holds the screen and what each second of attention is worth. */
export function WinnerPanel({ view }: { view: ScreenView }) {
    const auction = view.auction;
    const won = auction !== null && auction.winners.length > 0;
    return (
        <Pane label="Gana" ariaLabel="Ganador">
            {won ? (
                <div className="flex flex-1 flex-col gap-3">
                    <div className="flex flex-col gap-1">
                        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-section font-semibold tracking-tight">
                            {auction.winners.map((w, i) => (
                                <span
                                    key={w.campaign_id}
                                    className="inline-flex items-center gap-2"
                                >
                                    {i > 0 && <span className="text-muted">+</span>}
                                    <LogoFor view={view} brand={w.brand_name} size="md" />
                                    {w.brand_name}
                                </span>
                            ))}
                        </p>
                        {auction.offer && (
                            <p className="text-compact text-muted">
                                {auction.offer.label} · {moneyCl(auction.offer.price.amount)}
                            </p>
                        )}
                    </div>
                    <div className="mt-auto flex flex-col gap-0.5">
                        <span className="text-kpi font-semibold tracking-tight tabular-nums">
                            ${decimalCl(auction.bundle_value.amount, 2)}
                        </span>
                        <span className="text-compact text-muted">por segundo de atención</span>
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
    const options = auction?.alternatives ?? [];
    const max = Math.max(...options.map((o) => Number(o.value.amount)), 1);
    const winner = auction && auction.winners.length > 0 ? options[0] : undefined;
    return (
        <Pane label="Subasta" ariaLabel="Subasta en vivo">
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
                                        "flex min-w-0 items-center gap-2 text-compact",
                                        isWin && "font-semibold",
                                    )}
                                >
                                    <span className="flex shrink-0 items-center gap-1">
                                        {option.brands.map((brand) => (
                                            <LogoFor key={brand} view={view} brand={brand} />
                                        ))}
                                    </span>
                                    <span className="truncate">{name}</span>
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
