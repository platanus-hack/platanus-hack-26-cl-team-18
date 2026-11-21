import type { BookLine } from "@/contract";
import { setBidMultiplier, useBook } from "@/lib/market-api";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { useState } from "react";

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
        <main className="mx-auto flex min-h-screen max-w-md flex-col gap-6 bg-canvas p-4 text-body">
            <h1 className="text-section font-semibold">Bidding war</h1>
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
            <p className="text-muted">Pick the brand you bid for.</p>
            {lines.map((l) => (
                <button
                    key={l.campaign_id}
                    type="button"
                    className="rounded-control border border-border px-4 py-3 text-left font-medium"
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
    const [draft, setDraft] = useState<number | null>(null);
    const [failed, setFailed] = useState(false);
    const value = draft ?? Number(line.multiplier);
    const competitors = rivals.filter(
        (r) => r.category === line.category && r.campaign_id !== line.campaign_id,
    );
    const send = (next: number) => {
        setDraft(next);
        setBidMultiplier(line.campaign_id, next.toFixed(1))
            .then(() => {
                setFailed(false);
                setDraft(null);
                onSent();
            })
            .catch(() => {
                setFailed(true);
            });
    };
    return (
        <section className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
                <span className="text-section font-semibold">{line.brand_name}</span>
                {line.on_air ? (
                    <StatusBadge tone="success" label="On screen now" />
                ) : (
                    <StatusBadge tone="neutral" label="Not on screen" />
                )}
            </div>
            <label className="flex flex-col gap-2">
                <span className="text-muted">Bid multiplier</span>
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
            {failed && <StatusBadge tone="warning" label="Not sent, try again" />}
            <p className="text-metadata text-muted tabular-nums">
                Budget left {line.remaining.amount} {line.remaining.currency} · spent{" "}
                {line.spent.amount}
            </p>
            {competitors.length > 0 && (
                <ul className="flex flex-col gap-1 text-metadata">
                    {competitors.map((r) => (
                        <li key={r.campaign_id} className="flex justify-between">
                            <span>Rival {r.brand_name}</span>
                            <span className="tabular-nums">
                                ×{r.multiplier} {r.on_air ? "· on screen" : ""}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            <p className="text-metadata text-muted">
                Rivals never share a basket. A swap waits for the current teaser to finish.
            </p>
        </section>
    );
}
