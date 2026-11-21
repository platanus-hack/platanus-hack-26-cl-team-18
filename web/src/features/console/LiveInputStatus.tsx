import { backendHttpUrl } from "@/lib/config";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { useEffect, useState } from "react";
import { InputHealthSchema, inputLabels } from "./input-status";

export function LiveInputStatus({ screen }: { screen: string }) {
    const [health, setHealth] = useState<ReturnType<typeof InputHealthSchema.parse> | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        async function poll() {
            try {
                const response = await fetch(`${backendHttpUrl}/health`, {
                    signal: controller.signal,
                });
                if (!response.ok) throw new Error("health unavailable");
                const data: unknown = await response.json();
                if (!controller.signal.aborted) setHealth(InputHealthSchema.parse(data));
            } catch {
                if (!controller.signal.aborted) setHealth(null);
            } finally {
                if (!controller.signal.aborted)
                    timer = setTimeout(() => {
                        void poll();
                    }, 2000);
            }
        }
        void poll();
        return () => {
            controller.abort();
            clearTimeout(timer);
        };
    }, []);
    if (health === null)
        return <StatusBadge tone="warning" label="Estado de entradas no disponible" />;
    const labels = inputLabels(health, screen);
    const input = health.inputs[screen];
    return (
        <div className="flex flex-wrap items-center gap-2" aria-label={`Entradas · ${screen}`}>
            <span className="text-metadata text-muted">Entradas · {screen}</span>
            <StatusBadge
                tone={input?.camera_recent ? "success" : "warning"}
                label={labels.camera}
            />
            <StatusBadge tone={input?.speech_enabled ? "info" : "neutral"} label={labels.speech} />
            <StatusBadge
                tone={input?.player_connected ? "success" : "warning"}
                label={labels.player}
            />
        </div>
    );
}
