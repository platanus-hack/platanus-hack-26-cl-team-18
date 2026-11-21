import { cn } from "@/lib/cn";

/** Relvo's MetricCard value block: label, then a tabular KPI. Tone flags a breach in words too. */
export function Metric({
    label,
    value,
    tone = "default",
    hint,
}: {
    label: string;
    value: string;
    tone?: "default" | "warning" | undefined;
    hint?: string | undefined;
}) {
    return (
        <div className="min-w-0 space-y-1">
            <p className="text-compact text-muted">{label}</p>
            <p
                className={cn(
                    "text-kpi leading-tight font-medium tabular-nums",
                    tone === "warning" && "text-warning",
                )}
            >
                {value}
            </p>
            {hint && <p className="text-metadata text-muted">{hint}</p>}
        </div>
    );
}
