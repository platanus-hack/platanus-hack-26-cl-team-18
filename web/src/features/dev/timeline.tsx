import { cn } from "@/lib/cn";
import { Panel } from "@/shared/ui/Panel";
import type { Timeline } from "./dev-state";
import { providerFill } from "./format";

const HOT_FILL: Record<string, string> = {
    red: "bg-muted",
    decisión: "bg-accent",
    creatividad: "bg-info",
    pantalla: "bg-success",
    mejora: "bg-warning",
};

const span = (ms: number) => (ms < 1 ? "<1 ms" : `${Math.round(ms)} ms`);

/** Hot path (per creative) and warm jobs (per provider) on one time axis. */
export function TimelinePanel({
    timeline,
    windowMs,
}: {
    timeline: Timeline | null;
    windowMs: number;
}) {
    return (
        <Panel
            title="Línea de tiempo: camino caliente y tibio"
            aside={`últimos ${String(windowMs / 1000)} s`}
        >
            {!timeline ? (
                <p className="text-compact text-muted">
                    Sin decisiones ni trabajos tibios en la ventana.
                </p>
            ) : (
                <TimelineRows timeline={timeline} />
            )}
        </Panel>
    );
}

function TimelineRows({ timeline }: { timeline: Timeline }) {
    const width = timeline.endMs - timeline.startMs;
    const left = (ms: number) => `${String(Math.max(0, ((ms - timeline.startMs) / width) * 100))}%`;
    const size = (from: number, to: number) =>
        `${String(Math.max(0.3, ((to - Math.max(from, timeline.startMs)) / width) * 100))}%`;
    return (
        <div className="flex flex-col gap-1 text-metadata">
            <div className="grid grid-cols-[12rem_1fr] gap-2 text-muted tabular-nums">
                <span />
                <div className="flex justify-between">
                    <span>−{Math.round(width / 1000)} s</span>
                    <span>−{Math.round(width / 2000)} s</span>
                    <span>ahora</span>
                </div>
            </div>
            <ul className="flex flex-col gap-1" aria-label="Línea de tiempo">
                {timeline.rows.map((row) => (
                    <li key={row.id} className="grid grid-cols-[12rem_1fr] items-center gap-2">
                        <span
                            className={cn(
                                "truncate",
                                row.lane === "hot" ? "font-medium" : "text-muted",
                                row.state === "failed" && "text-danger",
                            )}
                            title={row.label}
                        >
                            {row.label}
                            {row.state === "failed" && " · falló"}
                            {row.state === "running" && " · corriendo"}
                        </span>
                        <div className="relative h-4 rounded-control bg-surface-sunken">
                            {row.segments.map((s, i) => {
                                const fill =
                                    row.lane === "hot"
                                        ? (HOT_FILL[s.label] ?? "bg-fg")
                                        : s.label === "en cola"
                                          ? "bg-border-strong"
                                          : row.state === "failed"
                                            ? "bg-danger"
                                            : providerFill(row.provider);
                                const label = `${s.label} ${span(s.toMs - s.fromMs)}`;
                                return (
                                    <span
                                        key={`${s.label}:${String(i)}`}
                                        className={cn(
                                            "absolute top-0 h-full rounded-control",
                                            fill,
                                            row.state === "running" &&
                                                row.lane === "warm" &&
                                                "opacity-70",
                                        )}
                                        style={{
                                            left: left(s.fromMs),
                                            width: size(s.fromMs, s.toMs),
                                        }}
                                        title={label}
                                        aria-label={label}
                                    />
                                );
                            })}
                        </div>
                    </li>
                ))}
            </ul>
            <p className="mt-2 text-muted">
                Caliente: red → decisión → creatividad → pantalla (mejora = actualización tibia).
                Tibio: gris en cola, color por proveedor (claude, vlm, elevenlabs, fal), rojo falló.
                Pasa el cursor para ver los ms.
            </p>
        </div>
    );
}
