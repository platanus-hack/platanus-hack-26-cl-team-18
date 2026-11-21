import type { DevOutbound } from "@/contract";
import { cn } from "@/lib/cn";
import { Button } from "@/shared/ui/button";
import { Panel } from "@/shared/ui/Panel";
import { Download, Pause, Play } from "lucide-react";
import { useState } from "react";
import type { LogEntry } from "./dev-state";
import { clock } from "./format";

const SHOWN = 200;

/** The session as a JSON file: facts only (keyframe bytes were never logged). */
export function exportSession(log: readonly LogEntry[], screen: string): string {
    return JSON.stringify(
        {
            exported_at: new Date().toISOString(),
            screen,
            note: "dev session export (docs/dev-console-plan.md); seed for evals/scenarios/",
            events: [...log].reverse().map((e) => ({
                received_at: new Date(e.receivedAtMs).toISOString(),
                message: e.message,
            })),
        },
        null,
        2,
    );
}

function download(text: string, filename: string) {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

export function EventLog({
    log,
    screen,
    paused,
    onPause,
}: {
    log: readonly LogEntry[];
    screen: string;
    paused: boolean;
    onPause: (paused: boolean) => void;
}) {
    const [hidden, setHidden] = useState<ReadonlySet<DevOutbound["type"]>>(new Set());
    const [open, setOpen] = useState<number | null>(null);
    const mine = log.filter((e) => e.screenId === null || e.screenId === screen);
    const types = [...new Set(mine.map((e) => e.type))].sort();
    const shown = mine.filter((e) => !hidden.has(e.type)).slice(0, SHOWN);
    const toggle = (type: DevOutbound["type"]) => {
        setHidden((current) => {
            const next = new Set(current);
            if (next.has(type)) next.delete(type);
            else next.add(type);
            return next;
        });
    };
    return (
        <Panel
            title="Registro de eventos"
            aside={`${String(shown.length)} de ${String(mine.length)}`}
        >
            <div className="mb-3 flex flex-wrap items-center gap-2">
                <Button
                    size="sm"
                    onClick={() => {
                        onPause(!paused);
                    }}
                    aria-pressed={paused}
                >
                    {paused ? (
                        <Play className="size-4" aria-hidden />
                    ) : (
                        <Pause className="size-4" aria-hidden />
                    )}
                    {paused ? "Reanudar" : "Pausar"}
                </Button>
                <Button
                    size="sm"
                    disabled={mine.length === 0}
                    onClick={() => {
                        download(exportSession(mine, screen), `dev-session-${screen}.json`);
                    }}
                >
                    <Download className="size-4" aria-hidden />
                    Exportar sesión
                </Button>
                {types.map((type) => (
                    <button
                        key={type}
                        type="button"
                        aria-pressed={!hidden.has(type)}
                        onClick={() => {
                            toggle(type);
                        }}
                        className={cn(
                            "rounded-full border px-2.5 py-0.5 font-mono text-metadata",
                            hidden.has(type)
                                ? "border-border-subtle text-muted line-through"
                                : "border-border-strong bg-surface-hover text-fg",
                        )}
                    >
                        {type}
                    </button>
                ))}
            </div>
            {shown.length === 0 ? (
                <p className="text-compact text-muted">
                    {paused ? "En pausa." : "Sin eventos todavía."}
                </p>
            ) : (
                <ul className="flex max-h-[60vh] flex-col overflow-auto font-mono text-metadata">
                    {shown.map((e) => (
                        <li key={e.id} className="border-b border-border-subtle last:border-0">
                            <button
                                type="button"
                                aria-expanded={open === e.id}
                                className="flex w-full gap-3 px-1 py-1 text-left hover:bg-surface-hover"
                                onClick={() => {
                                    setOpen(open === e.id ? null : e.id);
                                }}
                            >
                                <span className="text-muted tabular-nums">
                                    {clock(e.receivedAtMs)}
                                </span>
                                <span className="font-medium">{e.type}</span>
                            </button>
                            {open === e.id && (
                                <pre className="overflow-auto bg-surface-sunken p-2 whitespace-pre-wrap">
                                    {JSON.stringify(e.message, null, 2)}
                                </pre>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </Panel>
    );
}
