import { cn } from "@/lib/cn";
import { screenIdFromLocation } from "@/lib/config";
import { setConditions, setStock, useBook } from "@/lib/market-api";
import { Badge } from "@/shared/ui/badge";
import { Card } from "@/shared/ui/card";
import { AppShell, EmptyState, PageHeader } from "@/shared/ui/page";
import { useState } from "react";

// Demo weather (market-wide): the auction re-weighs ice cream vs. coffee at once.
const WEATHER_PRESETS = [
    { label: "Calor 33 °C", weather: { temperatureC: 33, raining: false } },
    { label: "Frío con lluvia 8 °C", weather: { temperatureC: 8, raining: true } },
    { label: "Clima real", weather: null },
] as const;

/** `/clerk?id=demo-01`: the store's stock tablet. A stock-out re-forms the basket at once. */
export function ClerkPage() {
    const screenId = screenIdFromLocation();
    const { book, error, refresh } = useBook(1000, screenId);
    const [failed, setFailed] = useState<string | null>(null);
    const lines = book?.lines ?? [];
    return (
        <AppShell width="narrow">
            <PageHeader
                title="Stock"
                description={`Pantalla ${screenId}. Un producto sin stock sale de la oferta al instante.`}
            />
            {error && (
                <Badge tone="danger" appearance="soft">
                    Sin conexión con la tienda
                </Badge>
            )}
            {failed && (
                <Badge tone="warning" appearance="soft">
                    No se guardó {failed}. Inténtalo otra vez.
                </Badge>
            )}
            <Card className="flex flex-wrap items-center gap-2 px-4 py-3">
                <span className="mr-auto font-medium">Clima</span>
                {WEATHER_PRESETS.map((preset) => (
                    <button
                        key={preset.label}
                        type="button"
                        className="rounded-md border border-control-edge px-3 py-1 text-compact hover:bg-surface-pressed"
                        onClick={() => {
                            setConditions(preset.weather)
                                .then(() => {
                                    setFailed(null);
                                })
                                .catch(() => {
                                    setFailed("el clima");
                                });
                        }}
                    >
                        {preset.label}
                    </button>
                ))}
            </Card>
            {lines.length === 0 ? (
                <Card>
                    <EmptyState
                        title="Sin productos todavía"
                        description="Aparecen cuando la tienda publica su catálogo."
                    />
                </Card>
            ) : (
                <Card className="divide-y divide-border-subtle">
                    {lines.map((line) => (
                        <div key={line.campaign_id} className="flex items-center gap-3 px-4 py-3">
                            <span className="font-medium">{line.brand_name}</span>
                            {line.on_air && <Badge tone="success">Al aire</Badge>}
                            <label className="ml-auto flex items-center gap-3 text-compact">
                                <span className={line.in_stock ? "text-fg" : "text-danger"}>
                                    {line.in_stock ? "Con stock" : "Sin stock"}
                                </span>
                                <button
                                    type="button"
                                    role="switch"
                                    aria-checked={line.in_stock}
                                    aria-label={`Stock de ${line.brand_name}`}
                                    className={cn(
                                        "relative h-6 w-11 shrink-0 rounded-full border transition-colors",
                                        line.in_stock
                                            ? "border-primary bg-primary"
                                            : "border-control-edge bg-surface-pressed",
                                    )}
                                    onClick={() => {
                                        setStock(screenId, line.brand_id, !line.in_stock)
                                            .then(() => {
                                                setFailed(null);
                                                refresh();
                                            })
                                            .catch(() => {
                                                setFailed(line.brand_name);
                                            });
                                    }}
                                >
                                    <span
                                        aria-hidden
                                        className={cn(
                                            "absolute top-0.5 left-0.5 size-4.5 rounded-full bg-fg shadow-resting transition-transform",
                                            line.in_stock && "translate-x-5 bg-ink",
                                        )}
                                    />
                                </button>
                            </label>
                        </div>
                    ))}
                </Card>
            )}
        </AppShell>
    );
}
