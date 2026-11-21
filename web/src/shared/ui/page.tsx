import { cn } from "@/lib/cn";
import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

const NAV = [
    { href: "/console", label: "Consola" },
    { href: "/campaigns", label: "Campañas" },
    { href: "/advertiser", label: "Pujas" },
    { href: "/clerk?id=demo-01", label: "Stock" },
    { href: "/screen?id=demo-01", label: "Pantalla" },
];

/** Relvo's app chrome, as a top bar: brand, then the tools. The player has no shell. */
export function AppShell({
    children,
    width = "wide",
}: {
    children: ReactNode;
    width?: "wide" | "narrow";
}) {
    const path = window.location.pathname;
    return (
        <div className="paper-theme">
            <header className="sticky top-0 z-(--z-overlay) border-b border-border bg-surface">
                <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
                    <a href="/" className="shrink-0 text-body font-semibold tracking-tight">
                        team18
                    </a>
                    <nav
                        aria-label="Principal"
                        className="-mx-1 flex min-w-0 gap-1 overflow-x-auto"
                    >
                        {NAV.map(({ href, label }) => {
                            const current = path === href.split("?")[0];
                            return (
                                <a
                                    key={href}
                                    href={href}
                                    aria-current={current ? "page" : undefined}
                                    className={cn(
                                        "shrink-0 rounded-control px-2.5 py-1.5 text-compact text-muted transition-colors hover:bg-surface-hover hover:text-fg",
                                        current && "bg-surface-hover font-medium text-fg",
                                    )}
                                >
                                    {label}
                                </a>
                            );
                        })}
                    </nav>
                </div>
            </header>
            <main
                className={cn(
                    "mx-auto flex flex-col gap-6 px-4 py-8 sm:px-6",
                    width === "wide" ? "max-w-7xl" : "max-w-xl",
                )}
            >
                {children}
            </main>
        </div>
    );
}

/** Relvo's PageHeader: title, optional one-line description, actions on the right. */
export function PageHeader({
    title,
    description,
    actions,
}: {
    title: string;
    description?: ReactNode;
    actions?: ReactNode;
}) {
    return (
        <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0 space-y-1">
                <h1 className="text-title leading-tight font-semibold tracking-tight">{title}</h1>
                {description && <p className="text-compact text-muted">{description}</p>}
            </div>
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
    );
}

/** Relvo's EmptyState: no frame, a selection-tinted icon tile, title, one sentence, one action. */
export function EmptyState({
    title,
    description,
    icon,
    action,
    size = "default",
}: {
    title: string;
    description?: string;
    icon?: ReactNode;
    action?: ReactNode;
    size?: "default" | "compact";
}) {
    const compact = size === "compact";
    return (
        <div
            className={cn(
                "flex gap-3",
                compact ? "items-start" : "flex-col items-center py-12 text-center",
            )}
        >
            <span
                aria-hidden
                className={cn(
                    "grid shrink-0 place-items-center rounded-control bg-accent-soft text-(--ui-selected-text) [&_svg]:size-4",
                    compact ? "size-8" : "size-10 [&_svg]:size-5",
                )}
            >
                {icon ?? <Inbox />}
            </span>
            <div className={cn("space-y-1", !compact && "max-w-sm")}>
                <p className="text-compact font-semibold">{title}</p>
                {description && <p className="text-compact text-muted">{description}</p>}
            </div>
            {action}
        </div>
    );
}
