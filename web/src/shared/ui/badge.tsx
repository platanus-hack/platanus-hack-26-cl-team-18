import { cn } from "@/lib/cn";
import { cva } from "class-variance-authority";
import type { HTMLAttributes } from "react";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

/**
 * Relvo's badge (shared/ui/badge.base.tsx). Each tone only sets three custom
 * properties; the appearance decides which it paints.
 * - `dot` (default): colored dot + body text. Lists and tables.
 * - `soft`: filled pill. The one status beside a title.
 * Status is never color alone: the label always says it.
 */
const badgeVariants = cva("inline-flex items-center gap-1.5 whitespace-nowrap font-medium", {
    variants: {
        tone: {
            success:
                "[--badge-edge:var(--state-success-edge)] [--badge-fill:var(--state-success-fill)] [--badge-ink:var(--state-success-ink)]",
            warning:
                "[--badge-edge:var(--state-warning-edge)] [--badge-fill:var(--state-warning-fill)] [--badge-ink:var(--state-warning-ink)]",
            danger: "[--badge-edge:var(--state-danger-edge)] [--badge-fill:var(--state-danger-fill)] [--badge-ink:var(--state-danger-ink)]",
            info: "[--badge-edge:var(--state-info-edge)] [--badge-fill:var(--state-info-fill)] [--badge-ink:var(--state-info-ink)]",
            neutral:
                "[--badge-edge:var(--state-neutral-edge)] [--badge-fill:var(--state-neutral-fill)] [--badge-ink:var(--state-neutral-ink)]",
        } satisfies Record<Tone, string>,
        appearance: {
            dot: "text-compact text-fg",
            soft: "rounded-full border border-(--badge-edge) bg-(--badge-fill) px-2.5 py-0.5 text-compact text-(--badge-ink)",
        },
    },
    defaultVariants: { tone: "neutral", appearance: "dot" },
});

export function Badge({
    tone = "neutral",
    appearance = "dot",
    className,
    children,
    ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone; appearance?: "dot" | "soft" }) {
    return (
        <span
            data-tone={tone}
            className={cn(badgeVariants({ tone, appearance }), className)}
            {...props}
        >
            <span
                aria-hidden
                className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    appearance === "dot" ? "bg-(--badge-ink)" : "bg-current",
                )}
            />
            {children}
        </span>
    );
}
