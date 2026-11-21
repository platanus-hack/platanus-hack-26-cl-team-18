import { cn } from "@/lib/cn";
import type { HTMLAttributes } from "react";

/** Relvo's card: white panel, strong hairline edge, no shadow. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn(
                "min-w-0 rounded-container border border-border-strong bg-surface",
                className,
            )}
            {...props}
        />
    );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn(
                "flex flex-wrap items-baseline justify-between gap-2 p-4 pb-3",
                className,
            )}
            {...props}
        />
    );
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
    return <h2 className={cn("text-section leading-tight font-semibold", className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
    return <div className={cn("p-4 pt-0", className)} {...props} />;
}
