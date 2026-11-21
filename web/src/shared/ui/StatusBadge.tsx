import { cn } from "@/lib/cn";
import { CircleAlert, CircleCheck, CircleDashed, CircleX, type LucideIcon } from "lucide-react";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const TONES: Record<Tone, { icon: LucideIcon; className: string }> = {
    success: { icon: CircleCheck, className: "bg-success-soft text-success" },
    warning: { icon: CircleAlert, className: "bg-warning-soft text-warning" },
    danger: { icon: CircleX, className: "bg-danger-soft text-danger" },
    info: { icon: CircleDashed, className: "bg-info-soft text-info" },
    neutral: { icon: CircleDashed, className: "bg-surface-raised text-muted" },
};

/** Status is never color alone: icon + label + color, always. */
export function StatusBadge({
    tone,
    label,
    className,
}: {
    tone: Tone;
    label: string;
    className?: string;
}) {
    const { icon: Icon, className: toneClass } = TONES[tone];
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 rounded-control px-2 py-0.5 text-metadata font-medium",
                toneClass,
                className,
            )}
        >
            <Icon aria-hidden className="size-3.5" />
            {label}
        </span>
    );
}
