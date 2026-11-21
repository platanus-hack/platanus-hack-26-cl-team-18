import { screenIdFromLocation } from "@/lib/config";
import { setStock, useBook } from "@/lib/market-api";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { WorkspaceHeader } from "@/shared/ui/WorkspaceHeader";
import { useState } from "react";

/** `/clerk?id=demo-01`: the store's stock tablet. A stock-out re-forms the basket at once. */
export function ClerkPage() {
    const screenId = screenIdFromLocation();
    const { book, error, refresh } = useBook(1000, screenId);
    const [failed, setFailed] = useState<string | null>(null);
    return (
        <div className="paper-theme">
            <WorkspaceHeader />
            <main className="operator-page flex flex-col gap-6 text-body">
                <header className="border-b border-rule pb-6">
                    <p className="eyebrow mb-3">Control de tienda · {screenId}</p>
                    <h1 className="page-heading">Disponibilidad</h1>
                    <p className="mt-3 text-muted">Ofrece solo lo que está en stock.</p>
                </header>
                {error && <StatusBadge tone="danger" label="Can't reach the store system" />}
                {failed && <StatusBadge tone="warning" label={`Not saved: ${failed}`} />}
                {!book?.lines.length && (
                    <p className="tool-card text-muted" role="status">
                        Esperando el catálogo de la tienda. Los productos aparecerán aquí cuando
                        estén disponibles.
                    </p>
                )}
                <ul className="flex flex-col gap-3">
                    {(book?.lines ?? []).map((line) => (
                        <li
                            key={line.campaign_id}
                            className="flex items-center justify-between rounded-container border border-border bg-surface px-4 py-4"
                        >
                            <span className="font-medium">
                                {line.brand_name}
                                {line.on_air && (
                                    <span className="ml-2 text-metadata text-muted">on screen</span>
                                )}
                            </span>
                            <button
                                type="button"
                                aria-pressed={line.in_stock}
                                className="min-w-28 rounded-control border border-border px-3 py-2"
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
                                <StatusBadge
                                    tone={line.in_stock ? "success" : "danger"}
                                    label={line.in_stock ? "In stock" : "Out of stock"}
                                />
                            </button>
                        </li>
                    ))}
                </ul>
            </main>
        </div>
    );
}
