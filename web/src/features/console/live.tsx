import { cn } from "@/lib/cn";
import { decimalCl, moneyCl } from "@/lib/format";
import type { ReactNode } from "react";
import { BrandLogo } from "./BrandLogo";
import type { ScreenView } from "./console-state";
import { runningTotals } from "./metrics";

/**
 * One column of the console's live stage: a plain title, an optional aside, the
 * content. No box of its own; the stage draws the hairlines between columns.
 */
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
                "flex min-w-0 flex-col gap-4 py-6 lg:px-7 lg:first:pl-0 lg:last:pr-0",
                className,
            )}
        >
            <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-compact font-medium text-muted">{label}</h2>
                {aside && <span className="text-metadata text-subtle tabular-nums">{aside}</span>}
            </div>
            {children}
        </section>
    );
}

/** A per-second rate, always with two decimals so a list of them lines up ("$6,89/s"). */
const perSecond = (amount: string) => `$${decimalCl(amount, 2)}/s`;

/** A brand's real logo from the book, looked up by its name (nothing when the book hasn't arrived). */
function LogoFor({ view, brand }: { view: ScreenView; brand: string }) {
    const line = view.book?.lines.find((l) => l.brand_name === brand);
    if (!line) return null;
    return <BrandLogo src={line.logo_url} accent={line.accent_color ?? null} size="sm" />;
}

/** "Subasta": every basket the auction weighed, ranked, the winner first. */
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
                                className="flex items-center gap-3 border-b border-border-subtle py-3.5 last:border-b-0"
                            >
                                <span className="flex shrink-0 items-center gap-1">
                                    {option.brands.map((brand) => (
                                        <LogoFor key={brand} view={view} brand={brand} />
                                    ))}
                                </span>
                                <span className="flex min-w-0 flex-1 flex-col gap-2">
                                    <span className={cn("truncate", isWin && "font-semibold")}>
                                        {name}
                                    </span>
                                    <span className="h-1 overflow-hidden rounded-full bg-border-subtle">
                                        <span
                                            className={cn(
                                                "block h-full rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none",
                                                isWin ? "bg-moment" : "bg-border-strong",
                                            )}
                                            style={{
                                                width: `${((value / max) * 100).toFixed(1)}%`,
                                            }}
                                        />
                                    </span>
                                </span>
                                <span
                                    className={cn(
                                        "shrink-0 font-mono tabular-nums",
                                        isWin ? "text-moment" : "text-muted",
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

/** A KPI's trend: one thin line, scaled to its own range, no axes. */
function Sparkline({ points }: { points: readonly number[] }) {
    if (points.length < 2) return <span aria-hidden className="block h-6" />;
    const min = Math.min(...points);
    const span = Math.max(...points) - min || 1;
    const d = points
        .map((p, i) => {
            const x = (i / (points.length - 1)) * 100;
            const y = 22 - ((p - min) / span) * 20;
            return `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
        })
        .join(" ");
    return (
        <svg
            aria-hidden
            viewBox="0 0 100 24"
            preserveAspectRatio="none"
            className="block h-6 w-full overflow-visible text-moment"
        >
            <path
                d={d}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
            />
        </svg>
    );
}

/** Seconds as a short count: "48 s", then minutes once it passes one. */
function duration(seconds: number): { value: string; unit: string } {
    if (seconds < 60) return { value: String(Math.round(seconds)), unit: "s" };
    return { value: decimalCl((seconds / 60).toFixed(1), 1), unit: "min" };
}

/** Three numbers anyone can read: time looked, who it fit, what brands paid. */
export function Meters({ view }: { view: ScreenView }) {
    const looked = duration(view.attentionSeconds);
    const coverage = view.auction?.coverage;
    const items = [
        {
            label: "Tiempo mirando",
            value: looked.value,
            unit: looked.unit,
            points: runningTotals(view.receipts, view.attentionSeconds, (r) =>
                Number(r.attention_seconds),
            ),
        },
        {
            label: "Interesados",
            value: coverage === undefined ? "—" : String(Math.round(coverage * 100)),
            unit: coverage === undefined ? null : "%",
            points: view.coverageHistory,
        },
        {
            label: "Pagado por las marcas",
            value: moneyCl(String(view.billed)),
            unit: "CLP",
            points: runningTotals(view.receipts, view.billed, (r) => Number(r.total.amount)),
        },
    ];
    return (
        <section aria-label="Sesión" className="grid gap-6 sm:grid-cols-3 sm:gap-0">
            {items.map((item) => (
                <div
                    key={item.label}
                    className="flex min-w-0 flex-col gap-2 sm:border-l sm:border-border sm:px-7 sm:first:border-l-0 sm:first:pl-0 sm:last:pr-0"
                >
                    <span className="text-compact text-muted">{item.label}</span>
                    <span className="flex items-baseline gap-1 font-mono text-kpi font-medium tracking-tight tabular-nums">
                        <span className="truncate">{item.value}</span>
                        {item.unit && (
                            <span className="font-sans text-compact font-normal tracking-normal text-subtle">
                                {item.unit}
                            </span>
                        )}
                    </span>
                    <Sparkline points={item.points} />
                </div>
            ))}
        </section>
    );
}
