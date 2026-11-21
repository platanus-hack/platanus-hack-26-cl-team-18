import type { ReactNode } from "react";
import { ErrorBoundary } from "./ErrorBoundary";

/** A console panel. Each one has its own error boundary: one crash can't blank the console. */
export function Panel({
    title,
    aside,
    children,
}: {
    title: string;
    aside?: ReactNode;
    children: ReactNode;
}) {
    return (
        <section className="flex min-h-0 flex-col rounded-container border border-t-2 border-border border-t-rule bg-surface shadow-resting">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-4">
                <h2 className="text-section font-semibold">{title}</h2>
                {aside}
            </header>
            <div className="min-h-0 flex-1 overflow-auto p-5 text-body">
                <ErrorBoundary label={title}>{children}</ErrorBoundary>
            </div>
        </section>
    );
}

export function Empty({ children }: { children: ReactNode }) {
    return <p className="text-muted">{children}</p>;
}
