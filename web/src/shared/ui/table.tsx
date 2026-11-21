import { cn } from "@/lib/cn";
import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";

/** Relvo's table (shared/ui/table.base.tsx): compact rows, sentence-case headers. */
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
    return (
        <div className="relative w-full overflow-auto">
            <table className={cn("w-full text-compact tabular-nums", className)} {...props} />
        </div>
    );
}

export function TableHeader(props: HTMLAttributes<HTMLTableSectionElement>) {
    return <thead className="border-b border-border-strong" {...props} />;
}

export function TableBody(props: HTMLAttributes<HTMLTableSectionElement>) {
    return <tbody className="[&_tr:last-child]:border-0" {...props} />;
}

export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
    return (
        <tr
            className={cn(
                "border-b border-border-subtle transition-colors [tbody_&]:hover:bg-surface-hover",
                className,
            )}
            {...props}
        />
    );
}

export function TableHead({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
    return (
        <th
            className={cn("px-3 py-2 text-left text-metadata font-medium text-muted", className)}
            {...props}
        />
    );
}

export function TableCell({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
    return <td className={cn("px-3 py-2 align-middle text-fg", className)} {...props} />;
}
